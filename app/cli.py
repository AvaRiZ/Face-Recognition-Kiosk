from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import time

import cv2

from core.config import AppConfig
from core.models import User
from core.state import AppStateManager
from database.repository import UserRepository
from services.embedding_service import count_embeddings, merge_embeddings_by_model, normalize_embeddings_by_model
from services.quality_service import FaceQualityService
from services.recognition_service import FaceRecognitionService
from services.tracking_service import TrackingService


def _coerce_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


class CLIApplication:
    def __init__(
        self,
        config: AppConfig,
        state: AppStateManager,
        repository: UserRepository,
        quality_service: FaceQualityService,
        recognition_service: FaceRecognitionService,
        tracking_service: TrackingService,
        yolo_model,
        yolo_device: str,
    ):
        self.config = config
        self.state = state
        self.repository = repository
        self.quality_service = quality_service
        self.recognition_service = recognition_service
        self.tracking_service = tracking_service
        self.yolo_model = yolo_model
        self.yolo_device = yolo_device

    def reload_users_from_database(self) -> None:
        self.state.load_users(self.repository.get_all_users())

    def connect_to_cctv_stream(self, stream_url, frame_width=640, frame_height=480, target_fps=30):
        print(f"Attempting to connect to: {stream_url}")

        if isinstance(stream_url, str) and stream_url.isdigit():
            cam_index = int(stream_url)
            if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
                cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(cam_index)
        else:
            cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(stream_url)

        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FPS, target_fps)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
            print("[OK] Successfully connected")
            return cap

        print("[WARN] Failed to connect")
        return None

    def process_cctv_stream(self, stream_url, frame_width=1280, frame_height=720):
        camera = self.connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
        if camera is None:
            return

        print("\n" + "=" * 50)
        print("CCTV FACE RECOGNITION SYSTEM")
        print("=" * 50)
        print("Press 'q' to quit")
        print("Press 'r' to reset recognition status")
        print("Press 'n' to check/add a new face (manual)")
        print("=" * 50)

        fps_counter = 0
        fps_start_time = time.time()
        current_fps = 0
        registration_prompted = False

        while True:
            success, frame = camera.read()
            if not success:
                print("[WARN] Lost connection to CCTV stream. Reconnecting...")
                camera = self.connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
                if camera is None:
                    break
                continue

            frame = cv2.resize(frame, (frame_width, frame_height))
            current_time = time.time()

            results = self.yolo_model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=0.3,
                imgsz=768,
                device=self.yolo_device,
                verbose=False,
            )

            face_crops = []
            face_qualities = []
            stable_faces = []

            for result in results:
                if result.boxes is None:
                    continue

                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    detection_confidence = float(box.conf[0]) if box.conf is not None else None

                    if (x2 - x1) < self.config.min_face_size or (y2 - y1) < self.config.min_face_size:
                        continue

                    face_crop = frame[y1:y2, x1:x2]
                    if face_crop.size == 0:
                        continue

                    quality_score, quality_status, quality_debug = self.quality_service.assess_face_quality(
                        face_crop,
                        detection_confidence=detection_confidence,
                    )
                    face_crops.append(face_crop)
                    face_qualities.append((quality_score, quality_status))

                    track_id = int(box.id[0]) if box.id is not None else None
                    face_id = track_id
                    if face_id is not None:
                        self.tracking_service.initialize_track_state(face_id, current_time)

                    is_stable = False
                    if face_id is not None:
                        is_stable = self.tracking_service.check_face_stability(face_id, x1, y1, x2, y2)

                    if is_stable:
                        stable_faces.append(
                            (
                                face_crop,
                                face_id,
                                detection_confidence,
                                (quality_score, quality_status, quality_debug),
                            )
                        )

                    if is_stable:
                        if quality_score >= self.config.face_quality_good_threshold:
                            color = (0, 255, 0)
                        elif quality_score >= self.config.face_quality_threshold:
                            color = (0, 255, 255)
                        else:
                            color = (0, 0, 255)
                    else:
                        color = (128, 128, 128)

                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                    track_text = f"T{face_id}" if face_id is not None else "T?"
                    stability_text = "NO-ID" if face_id is None else ("STABLE" if is_stable else "MOVING")
                    cv2.putText(
                        frame,
                        f"{track_text} {stability_text} Q:{quality_score:.1f}",
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        color,
                        1,
                    )

                    track_state = self.state.get_track_state(face_id) if face_id is not None else None
                    if track_state and track_state.recognized and track_state.user:
                        confidence_text = track_state.user.get("confidence")
                        identity_text = (
                            f"{track_state.user['name']} ({confidence_text})"
                            if confidence_text
                            else track_state.user["name"]
                        )
                        identity_color = (0, 255, 0)
                    elif track_state and track_state.last_recognition_time > 0.0:
                        identity_text = "Unknown"
                        identity_color = (0, 165, 255)
                    else:
                        identity_text = "Untracked" if face_id is None else "Tracking"
                        identity_color = (180, 180, 180)

                    label_y = min(y2 + 15, frame_height - 10)
                    cv2.putText(
                        frame,
                        identity_text,
                        (x1, label_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        identity_color,
                        1,
                    )

            stale_track_ids = self.tracking_service.cleanup_stale_tracks(current_time)
            reg_state = self.state.registration_state
            if reg_state.manual_track_id in stale_track_ids:
                self.state.stop_manual_registration()
                print("Manual registration track lost. Capture canceled.")

            for face_crop, face_id, detection_confidence, quality_tuple in stable_faces:
                if face_id is None or reg_state.in_progress:
                    continue

                track_state = self.tracking_service.initialize_track_state(face_id, current_time)

                if reg_state.manual_requested and not reg_state.manual_active and track_state.recognized:
                    print("Face already in database. Manual registration canceled.")
                    reg_state.manual_requested = False
                    continue

                if track_state.recognized:
                    continue

                if (current_time - track_state.last_recognition_time) < self.config.recognition_cooldown_seconds:
                    continue

                track_state.last_recognition_time = current_time
                track_state.last_seen = current_time

                if reg_state.manual_requested and not reg_state.manual_active:
                    result = self.recognition_service.register_or_recognize_face(
                        face_crop,
                        quality_service=self.quality_service,
                        face_id=face_id,
                        allow_registration=False,
                        detection_confidence=detection_confidence,
                        precomputed_quality=quality_tuple,
                    )
                    if result is None:
                        continue
                    if result:
                        track_state.recognized = True
                        track_state.user = dict(self.state.recognized_user) if self.state.recognized_user else None
                        track_state.last_seen = current_time
                        track_state.last_recognition_time = current_time
                        print("Face already in database. Manual registration canceled.")
                        reg_state.manual_requested = False
                    else:
                        track_state.recognized = False
                        track_state.user = None
                        track_state.last_seen = current_time
                        track_state.last_recognition_time = current_time
                        print("Unknown face. Capturing 3 samples for registration...")
                        self.state.start_manual_registration(face_id)
                        self.recognition_service.register_or_recognize_face(
                            face_crop,
                            quality_service=self.quality_service,
                            face_id=face_id,
                            allow_registration=True,
                            detection_confidence=detection_confidence,
                            precomputed_quality=quality_tuple,
                        )
                        if reg_state.in_progress:
                            self.state.stop_manual_registration()
                    continue

                if reg_state.manual_active and reg_state.manual_track_id is not None and face_id != reg_state.manual_track_id:
                    continue

                allow_registration = reg_state.manual_active and (face_id == reg_state.manual_track_id)
                result = self.recognition_service.register_or_recognize_face(
                    face_crop,
                    quality_service=self.quality_service,
                    face_id=face_id,
                    allow_registration=allow_registration,
                    detection_confidence=detection_confidence,
                    precomputed_quality=quality_tuple,
                )

                if result is True:
                    track_state.recognized = True
                    track_state.user = dict(self.state.recognized_user) if self.state.recognized_user else None
                    track_state.last_seen = current_time
                    track_state.last_recognition_time = current_time
                elif result is False:
                    track_state.recognized = False
                    track_state.user = None
                    track_state.last_seen = current_time
                    track_state.last_recognition_time = current_time

                if reg_state.manual_active:
                    if result is True:
                        self.state.stop_manual_registration()
                        print("Face already in database. Manual registration canceled.")
                    elif reg_state.in_progress:
                        self.state.stop_manual_registration()

            if reg_state.in_progress and reg_state.pending_registration and not registration_prompted:
                registration_prompted = True
                self.handle_registration()
                self.state.set_recognized_user(None)
                if not reg_state.in_progress:
                    registration_prompted = False

            for i, (crop, (quality_score, _quality_status)) in enumerate(zip(face_crops[:5], face_qualities[:5])):
                crop_h, crop_w = crop.shape[:2]
                scale = 80 / crop_h
                thumbnail = cv2.resize(crop, (int(crop_w * scale), 80))
                x_start = 10 + i * 90
                x_end = min(x_start + thumbnail.shape[1], frame_width)
                y_start = 80
                y_end = min(y_start + thumbnail.shape[0], frame_height)
                frame[y_start:y_end, x_start:x_end] = thumbnail[: y_end - y_start, : x_end - x_start]
                cv2.putText(
                    frame,
                    f"{quality_score:.1f}",
                    (x_start, y_end + 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 255, 255),
                    1,
                )

            fps_counter += 1
            if time.time() - fps_start_time >= 1.0:
                current_fps = fps_counter
                fps_counter = 0
                fps_start_time = time.time()

            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (frame_width, 70), (0, 0, 0), -1)
            cv2.rectangle(overlay, (0, frame_height - 50), (frame_width, frame_height), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

            cv2.putText(
                frame,
                "Controls: [N] New User  [R] Reset  [Q] Quit",
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
            )
            cv2.putText(
                frame,
                f"DB Users: {self.state.user_count}   FPS: {current_fps}",
                (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (200, 200, 200),
                1,
            )

            if self.state.recognized_user:
                status_text = (
                    f"Recognized: {self.state.recognized_user['name']} "
                    f"({self.state.recognized_user['confidence']})"
                )
                status_color = (0, 255, 0)
            elif reg_state.in_progress:
                status_text = (
                    "Registration ready - "
                    f"{reg_state.capture_count}/{reg_state.max_captures} samples"
                )
                status_color = (0, 165, 255)
            elif reg_state.manual_active:
                status_text = (
                    "Manual capture in progress: "
                    f"{reg_state.capture_count}/{reg_state.max_captures}"
                )
                status_color = (0, 165, 255)
            elif reg_state.manual_requested:
                status_text = "Manual check requested - Hold still"
                status_color = (0, 165, 255)
            else:
                status_text = "Scanning for faces..."
                status_color = (255, 255, 255)

            cv2.putText(
                frame,
                status_text,
                (10, frame_height - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                status_color,
                2,
            )

            cv2.imshow("CCTV Face Recognition", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("\nShutting down...")
                break
            if key == ord("r"):
                self.state.set_recognized_user(None)
                self.state.stop_manual_registration()
                registration_prompted = False
                self.state.clear_tracking_state()
                print("Recognition status reset")
            if key == ord("n"):
                if reg_state.in_progress:
                    print("Registration already in progress. Finish it before starting a new one.")
                else:
                    self.state.request_manual_registration()
                    registration_prompted = False
                    print("Manual new-user check requested. Hold still for recognition...")

        camera.release()
        cv2.destroyAllWindows()

    def handle_registration(self):
        reg_state = self.state.registration_state
        if not reg_state.pending_registration:
            print("No pending registration")
            return

        print("\n" + "=" * 50)
        print("NEW USER REGISTRATION")
        print("=" * 50)
        print(f"Captured {len(reg_state.pending_registration)} face samples")

        name = input("Enter full name: ").strip()
        sr_code = input("Enter SR Code: ").strip()
        course = input("Enter course: ").strip()

        if not name or not sr_code or not course:
            print("Error: All fields are required")
            return

        existing = self.repository.get_user_by_sr_code(sr_code)
        if existing:
            print(f"Warning: SR Code {sr_code} already registered to {existing.name}")
            choice = input("Update existing user? (y/n): ").lower()
            if choice != "y":
                return

        all_embeddings = {}
        image_paths = []

        for i, face_sample in enumerate(reg_state.pending_registration):
            timestamp = int(time.time() * 1000)
            user_folder = os.path.join(self.config.base_save_dir, sr_code)
            os.makedirs(user_folder, exist_ok=True)
            filename = os.path.join(user_folder, f"face_{timestamp}_{i}.jpg")
            cv2.imwrite(filename, face_sample.face_crop)
            image_paths.append(filename)
            all_embeddings = merge_embeddings_by_model(all_embeddings, face_sample.embeddings)

        if all_embeddings:
            user_id = self.repository.save_user(
                User(
                    id=existing.id if existing else 0,
                    name=name,
                    sr_code=sr_code,
                    course=course,
                    embeddings=all_embeddings,
                    image_paths=image_paths,
                    embedding_dim=0,
                )
            )
            saved_user = self.repository.get_user_by_sr_code(sr_code)
            if saved_user:
                saved_user.id = user_id
                self.state.replace_user(saved_user)

            total_embeddings = count_embeddings(normalize_embeddings_by_model(all_embeddings))
            print(f"[OK] Registered {name} with {total_embeddings} embeddings across models")

        self.state.complete_registration()

    def main_menu(self):
        while True:
            print("\n" + "=" * 50)
            print("CCTV FACE RECOGNITION SYSTEM")
            print("=" * 50)
            print(f"Users in database: {self.state.user_count}")
            print(f"Models: {self.config.primary_model} + {self.config.secondary_model}")
            print("=" * 50)
            print("1. Start CCTV Face Recognition")
            print("2. Register New User (Webcam)")
            print("3. List Registered Users")
            print("4. Delete User")
            print("5. View Statistics")
            print("6. Reset Database")
            print("7. Exit")
            print("=" * 50)

            choice = input("\nEnter your choice (1-7): ").strip()

            if choice == "1":
                stream_url = input("Enter CCTV stream URL (or camera index, press Enter for 0): ").strip()
                if not stream_url:
                    stream_url = "0"
                self.process_cctv_stream(stream_url)

                if self.state.registration_state.in_progress:
                    self.handle_registration()

            elif choice == "2":
                print("\nStarting webcam registration...")
                subprocess.run([sys.executable, "register_fixed.py"])
                self.reload_users_from_database()

            elif choice == "3":
                print("\n" + "=" * 50)
                print("REGISTERED USERS")
                print("=" * 50)
                if not self.state.users:
                    print("No users registered")
                else:
                    for user in self.state.users:
                        print(f"ID: {user.id:3d} | Name: {user.name:20s} | SR Code: {user.sr_code}")
                print("=" * 50)

            elif choice == "4":
                try:
                    user_id = int(input("Enter User ID to delete (0 to cancel): "))
                    if user_id == 0:
                        continue

                    user = self.repository.get_user_by_id(user_id)
                    if not user:
                        print(f"User ID {user_id} not found")
                        continue

                    print(f"Delete user: {user.name} ({user.sr_code})?")
                    confirm = input("Confirm (y/n): ").lower()
                    if confirm != "y":
                        print("Deletion cancelled")
                        continue

                    self.repository.delete_user(user_id)
                    self.state.remove_user(user_id)
                    print("User deleted")
                except ValueError:
                    print("Invalid input")

            elif choice == "5":
                stats = self.repository.get_recognition_statistics()
                print("\n" + "=" * 50)
                print("RECOGNITION STATISTICS")
                print("=" * 50)
                print(f"Method: Two-Factor ({self.config.primary_model} + {self.config.secondary_model})")
                print(
                    f"Thresholds: {self.config.primary_model}>={self.config.primary_threshold:.2f}, "
                    f"{self.config.secondary_model}>={self.config.secondary_threshold:.2f}"
                )
                print("-" * 50)

                if not stats:
                    print("No recognition data")
                else:
                    for user_id, name, sr_code, embedding_dim, emb_blob, rec_count, avg_conf, best_conf, last_seen in stats:
                        embeddings_by_model = {}
                        if emb_blob:
                            try:
                                embeddings_by_model = normalize_embeddings_by_model(pickle.loads(emb_blob))
                            except Exception:
                                embeddings_by_model = {}

                        model_counts = {
                            model_name: len(emb_list)
                            for model_name, emb_list in embeddings_by_model.items()
                            if emb_list
                        }
                        total_embeddings = sum(model_counts.values())
                        model_counts_str = (
                            ", ".join(f"{model}:{count}" for model, count in model_counts.items())
                            if model_counts
                            else "N/A"
                        )

                        latest = self.repository.get_latest_recognition_detail(user_id)
                        avg_conf_num = _coerce_float(avg_conf)
                        best_conf_num = _coerce_float(best_conf)
                        avg = f"{avg_conf_num:.2%}" if avg_conf_num is not None else "N/A"
                        best = f"{best_conf_num:.2%}" if best_conf_num is not None else "N/A"
                        last_seen_str = last_seen if last_seen else "Never"

                        print(f"Name: {name} ({sr_code})")
                        print(f"  Recognitions: {rec_count} | Avg Confidence: {avg} | Best Confidence: {best}")
                        print(
                            f"  Embeddings: total={total_embeddings}, dim={embedding_dim}, "
                            f"by_model=[{model_counts_str}]"
                        )
                        print(f"  Last Recognition: {last_seen_str}")

                        if latest:
                            ts, method, p_conf, s_conf, p_dist, s_dist, face_quality = latest
                            p_conf = _coerce_float(p_conf)
                            s_conf = _coerce_float(s_conf)
                            p_dist = _coerce_float(p_dist)
                            s_dist = _coerce_float(s_dist)
                            face_quality = _coerce_float(face_quality)

                            p_conf_s = f"{p_conf:.2%}" if p_conf is not None else "N/A"
                            s_conf_s = f"{s_conf:.2%}" if s_conf is not None else "N/A"
                            p_dist_s = f"{p_dist:.4f}" if p_dist is not None else "N/A"
                            s_dist_s = f"{s_dist:.4f}" if s_dist is not None else "N/A"
                            quality_s = f"{face_quality:.2f}" if face_quality is not None else "N/A"
                            print(
                                "  Latest Match Details: "
                                f"method={method or 'N/A'}, "
                                f"{self.config.primary_model}_conf={p_conf_s}, "
                                f"{self.config.secondary_model}_conf={s_conf_s}, "
                                f"{self.config.primary_model}_dist={p_dist_s}, "
                                f"{self.config.secondary_model}_dist={s_dist_s}, "
                                f"face_quality={quality_s}, timestamp={ts}"
                            )
                        else:
                            print("  Latest Match Details: N/A")
                        print("-" * 50)

                print("=" * 50)

            elif choice == "6":
                print("\nWARNING: This will delete ALL users and data")
                confirm = input("Type 'YES' to confirm: ")
                if confirm == "YES":
                    self.repository.reset_database()
                    if os.path.exists(self.config.base_save_dir):
                        shutil.rmtree(self.config.base_save_dir)
                    os.makedirs(self.config.base_save_dir, exist_ok=True)
                    self.state.reset_database_state()
                    print("Database reset complete")
                else:
                    print("Reset cancelled")

            elif choice == "7":
                print("\nExiting... Goodbye")
                break
            else:
                print("Invalid choice")
