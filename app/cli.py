from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import threading
import time

import cv2

from core.config import AppConfig
from core.models import User
from core.state import AppStateManager
from database.repository import UserRepository
from services.dataset_service import DetectorDatasetService
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
        self.detector_dataset_service = DetectorDatasetService(config)
        self._detection_pause_event = threading.Event()
        self._pause_notice_shown = False

    def reload_users_from_database(self) -> None:
        self.state.load_users(self.repository.get_all_users())

    def pause_detection(self) -> None:
        self._detection_pause_event.set()

    def resume_detection(self) -> None:
        self._detection_pause_event.clear()
        self._pause_notice_shown = False

    def detection_paused(self) -> bool:
        return self._detection_pause_event.is_set()

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
        print("Registration is available on the website only")
        print("=" * 50)

        fps_counter = 0
        fps_start_time = time.time()
        current_fps = 0
        frame_index = 0
        last_results = None
        detection_interval = max(int(self.config.detection_every_n_frames), 1)
        saved_real_val_frames = self.detector_dataset_service.count_real_val_frames()

        while True:
            if self.detection_paused():
                if camera is not None:
                    camera.release()
                    camera = None
                    cv2.destroyAllWindows()
                if not self._pause_notice_shown:
                    print("[INFO] Detection paused so the website registration camera can open.")
                    self._pause_notice_shown = True
                time.sleep(0.2)
                continue

            if camera is None:
                camera = self.connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
                if camera is None:
                    time.sleep(1.0)
                    continue
                last_results = None
                if self._pause_notice_shown:
                    print("[INFO] Detection resumed after website registration camera release.")
                    self._pause_notice_shown = False

            success, frame = camera.read()
            if not success:
                print("[WARN] Lost connection to CCTV stream. Reconnecting...")
                camera = self.connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
                last_results = None
                if camera is None:
                    break
                continue

            frame = cv2.resize(frame, (frame_width, frame_height))
            current_time = time.time()
            frame_index += 1

            if (
                self.config.real_val_capture_enabled
                and saved_real_val_frames < self.config.real_val_capture_max_frames
                and frame_index % max(self.config.real_val_capture_every_n_frames, 1) == 0
            ):
                saved_frame_path = self.detector_dataset_service.save_real_val_frame(frame, frame_index)
                if saved_frame_path:
                    saved_real_val_frames += 1
                    print(f"[OK] Saved real validation frame: {saved_frame_path}")

            should_run_detection = True
            if self.config.enable_detection_frame_scheduling and detection_interval > 1:
                should_run_detection = (frame_index % detection_interval == 0) or (last_results is None)

            if should_run_detection:
                last_results = self.yolo_model.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    conf=0.3,
                    imgsz=768,
                    device=self.yolo_device,
                    verbose=False,
                )

            results = last_results or []

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
                        if self.tracking_service.refresh_track_geometry(face_id, (x1, y1, x2, y2)):
                            print(f"[INFO] Track {face_id} geometry changed sharply. Resetting carried identity.")

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

            self.tracking_service.cleanup_stale_tracks(current_time)

            for face_crop, face_id, detection_confidence, quality_tuple in stable_faces:
                if face_id is None:
                    continue

                track_state = self.tracking_service.initialize_track_state(face_id, current_time)

                if track_state.recognized:
                    continue

                if (current_time - track_state.last_recognition_time) < self.config.recognition_cooldown_seconds:
                    continue

                track_state.last_recognition_time = current_time
                track_state.last_seen = current_time

                result = self.recognition_service.register_or_recognize_face(
                    face_crop,
                    quality_service=self.quality_service,
                    face_id=face_id,
                    allow_registration=False,
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
                "Controls: [R] Reset  [Q] Quit",
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
            )
            cv2.putText(
                frame,
                f"DB Users: {self.state.user_count}   FPS: {current_fps}   Val Frames: {saved_real_val_frames}",
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
                self.state.clear_tracking_state()
                print("Recognition status reset")

        camera.release()
        cv2.destroyAllWindows()

    def handle_registration(self):
        print("Terminal registration is disabled. Please use the website register page.")

    def main_menu(self):
        stream_url = os.environ.get("CCTV_STREAM_URL", "0").strip() or "0"
        print("The website is running at the same time with detection and recognition.")
        print("The register is only on the website, there should be no options on the terminal.")
        self.process_cctv_stream(stream_url)
