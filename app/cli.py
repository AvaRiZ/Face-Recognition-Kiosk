from __future__ import annotations

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
from utils.image_utils import crop_face_region


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
        self._stream_status_lock = threading.Lock()
        self._stream_status = {
            "state": "initializing",
            "message": "Initializing camera stream.",
            "last_frame_ts": None,
            "updated_at": time.time(),
        }

    def _set_stream_status(self, state: str, message: str, last_frame_ts: float | None = None) -> None:
        with self._stream_status_lock:
            self._stream_status.update(
                {
                    "state": state,
                    "message": message,
                    "last_frame_ts": last_frame_ts,
                    "updated_at": time.time(),
                }
            )

    def get_stream_status(self) -> dict:
        with self._stream_status_lock:
            snapshot = dict(self._stream_status)

        last_frame_ts = snapshot.get("last_frame_ts")
        if isinstance(last_frame_ts, (int, float)):
            snapshot["last_frame_age_seconds"] = max(0.0, time.time() - float(last_frame_ts))
        else:
            snapshot["last_frame_age_seconds"] = None

        snapshot.pop("last_frame_ts", None)
        return snapshot

    def toggle_quality_debug(self) -> bool:
        self.config.quality_debug_enabled = not self.config.quality_debug_enabled
        return self.config.quality_debug_enabled

    def reload_users_from_database(self) -> None:
        self.state.load_users(self.repository.get_all_users())

    def pause_detection(self) -> None:
        self._detection_pause_event.set()

    def resume_detection(self) -> None:
        self._detection_pause_event.clear()
        self._pause_notice_shown = False

    def detection_paused(self) -> bool:
        return self._detection_pause_event.is_set()

    def connect_to_cctv_stream(self, stream_source, frame_width=1280, frame_height=720, target_fps=30):
        print(f"Attempting to connect to: {stream_source}")
        self._set_stream_status("connecting", "Connecting to CCTV stream...")

        if isinstance(stream_source, str):
            stream_source = stream_source.strip()

        if isinstance(stream_source, str) and stream_source.isdigit():
            stream_source = int(stream_source)

        if isinstance(stream_source, int):
            cam_index = stream_source
            if sys.platform.startswith("win") and hasattr(cv2, "CAP_DSHOW"):
                cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(cam_index)
        else:
            cap = cv2.VideoCapture(stream_source, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(stream_source)

        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FPS, target_fps)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
            print("[OK] Successfully connected")
            self._set_stream_status("connected", "Camera stream connected.")
            return cap

        print("[WARN] Failed to connect")
        self._set_stream_status("disconnected", "Unable to connect to CCTV stream.")
        return None

    @staticmethod
    def _extract_landmarks_from_result(result_obj, detection_index: int, bbox):
        if result_obj is None or getattr(result_obj, "keypoints", None) is None:
            return None

        keypoints_xy = getattr(result_obj.keypoints, "xy", None)
        if keypoints_xy is None:
            return None

        try:
            if hasattr(keypoints_xy, "detach"):
                keypoints_xy = keypoints_xy.detach().cpu().numpy()
            else:
                keypoints_xy = keypoints_xy.cpu().numpy() if hasattr(keypoints_xy, "cpu") else keypoints_xy
        except Exception:
            return None

        if detection_index < 0 or detection_index >= len(keypoints_xy):
            return None

        points = keypoints_xy[detection_index]
        if points is None or len(points) < 3:
            return None

        x1, y1, _x2, _y2 = bbox

        def _to_crop_pt(idx):
            if idx >= len(points):
                return None
            px, py = points[idx][:2]
            return float(px - x1), float(py - y1)

        landmarks = {
            "left_eye": _to_crop_pt(0),
            "right_eye": _to_crop_pt(1),
            "nose": _to_crop_pt(2),
            "mouth_left": _to_crop_pt(3),
            "mouth_right": _to_crop_pt(4),
        }
        if any(value is not None for value in landmarks.values()):
            return landmarks
        return None

    @staticmethod
    def _bbox_area(bbox: tuple[int, int, int, int] | None) -> int:
        if bbox is None:
            return 0
        x1, y1, x2, y2 = bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    def _should_run_detection(self, frame_index: int) -> bool:
        return frame_index % max(1, int(self.config.detection_every_n_frames)) == 0

    def _distance_feedback(self, bbox_area: int) -> str | None:
        min_area = int(self.config.registration_min_face_area)
        if bbox_area <= 0:
            return None
        if bbox_area < min_area:
            return "Move closer"
        if bbox_area > int(min_area * 2.2):
            return "Move slightly back"
        return None

    @staticmethod
    def _pose_instruction_text(pose: str | None) -> str:
        pose_map = {
            "front": "Face Forward",
            "left": "Look Left",
            "right": "Look Right",
        }
        return pose_map.get((pose or "").lower(), "Hold Still")

    def _build_identity_label(self, track_state, reg_state, is_selected_for_registration: bool):
        if track_state is None:
            return "Untracked", (180, 180, 180)

        if track_state.recognized and track_state.user:
            confidence_text = track_state.user.get("confidence")
            label = f"Recognized: {track_state.user['name']}"
            if confidence_text:
                label = f"{label} ({confidence_text})"
            return label, (0, 255, 0)

        if is_selected_for_registration and reg_state.manual_active:
            if track_state.last_quality_score < self.config.face_quality_threshold:
                return "Unknown (low quality)", (0, 140, 255)
            expected_pose = self.state.get_current_registration_pose()
            if expected_pose and track_state.last_pose and track_state.last_pose != expected_pose:
                return "Unknown (pose mismatch)", (0, 165, 255)

        if track_state.last_recognition_confidence is not None:
            threshold = track_state.last_recognition_threshold or self.config.recognition_confidence_threshold
            if track_state.last_recognition_confidence < threshold:
                return "Recognition uncertain", (0, 215, 255)

        if track_state.failed_good_quality_attempts >= self.config.unknown_person_attempt_threshold:
            return "Unknown person", (0, 165, 255)

        if track_state.last_quality_score < self.config.face_quality_threshold:
            return "Unknown (low quality)", (0, 140, 255)

        if is_selected_for_registration and reg_state.manual_active:
            expected_pose = self.state.get_current_registration_pose()
            if expected_pose and track_state.last_pose != expected_pose:
                return "Unknown (pose mismatch)", (0, 165, 255)

        return "Tracking", (180, 180, 180)

    def _select_registration_candidate(self, visible_track_ids: list[int]):
        reg_state = self.state.registration_state
        candidate_ids: list[int] = []

        for track_id in visible_track_ids:
            track_state = self.state.get_track_state(track_id)
            if track_state is None or not track_state.last_stable:
                continue
            if track_state.last_area <= 0:
                continue
            candidate_ids.append(track_id)

        for _track_id, track_state in self.state.tracked_faces.items():
            track_state.selected_for_registration = False

        if reg_state.manual_active and reg_state.manual_track_id in candidate_ids:
            selected_id = reg_state.manual_track_id
        else:
            selected_id = None
            previous_id = reg_state.selected_track_id
            previous_area = 0
            if previous_id is not None:
                previous_state = self.state.get_track_state(previous_id)
                previous_area = previous_state.last_area if previous_state is not None else 0

            largest_area = 0
            for track_id in candidate_ids:
                track_state = self.state.get_track_state(track_id)
                if track_state is None:
                    continue
                if track_state.last_area > largest_area:
                    largest_area = track_state.last_area
                    selected_id = track_id

            if previous_id in candidate_ids and previous_area > 0 and previous_area >= int(largest_area * 0.85):
                selected_id = previous_id

        reg_state.selected_track_id = selected_id
        if selected_id is not None:
            selected_state = self.state.get_track_state(selected_id)
            if selected_state is not None:
                selected_state.selected_for_registration = True
        return selected_id

    @staticmethod
    def _draw_text_block(frame, lines, origin_x, origin_y, color, scale=0.65, thickness=2, line_gap=26):
        y = origin_y
        for line in lines:
            cv2.putText(
                frame,
                line,
                (origin_x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                color,
                thickness,
            )
            y += line_gap

    def _draw_registration_guidance(self, frame, frame_height: int, selected_track_id: int | None):
        reg_state = self.state.registration_state
        if selected_track_id is None or not (reg_state.manual_requested or reg_state.manual_active):
            return

        track_state = self.state.get_track_state(selected_track_id)
        if track_state is None:
            return

        expected_pose = self.state.get_current_registration_pose() or "front"
        lines = [f"Expected pose: {self._pose_instruction_text(expected_pose)}"]

        distance_feedback = self._distance_feedback(track_state.last_area)
        if distance_feedback:
            lines.append(distance_feedback)

        if track_state.last_pose and track_state.last_pose != expected_pose:
            lines.append(f"Detected pose: {self._pose_instruction_text(track_state.last_pose)}")
            lines.append(f"Please: {self._pose_instruction_text(expected_pose)}")

        self._draw_text_block(frame, lines, 10, frame_height - 110, (0, 220, 255), scale=0.7, thickness=2)

    def process_cctv_stream(self, stream_source=None, frame_width=1280, frame_height=720):
        if stream_source is None:
            stream_source = self.config.resolved_cctv_stream_source()

        camera = self.connect_to_cctv_stream(stream_source, frame_width, frame_height, target_fps=30)
        if camera is None:
            self._set_stream_status("disconnected", "Camera stream is unavailable.")
            return

        print("\n" + "=" * 50)
        print("CCTV FACE RECOGNITION SYSTEM")
        print("=" * 50)
        print("Press 'q' to quit")
        print("Press 'd' to toggle quality debug")
        print("=" * 50)

        fps_counter = 0
        fps_start_time = time.time()
        current_fps = 0
        frame_index = 0
        saved_real_val_frames = self.detector_dataset_service.count_real_val_frames()
        registration_prompted = False
        last_visible_track_ids: list[int] = []
        last_face_crops = []
        last_face_qualities = []

        while True:
            if self.detection_paused():
                if camera is not None:
                    camera.release()
                    camera = None
                    cv2.destroyAllWindows()
                if not self._pause_notice_shown:
                    print("[INFO] Detection paused so the website registration camera can open.")
                    self._pause_notice_shown = True
                self._set_stream_status("paused", "Detection is paused while website capture uses the camera.")
                time.sleep(0.2)
                continue

            if camera is None:
                camera = self.connect_to_cctv_stream(stream_source, frame_width, frame_height, target_fps=30)
                if camera is None:
                    self._set_stream_status("reconnecting", "Retrying camera stream connection...")
                    time.sleep(1.0)
                    continue
                if self._pause_notice_shown:
                    print("[INFO] Detection resumed after website registration camera release.")
                    self._pause_notice_shown = False
                self._set_stream_status("live", "Camera stream active and running.")

            success, frame = camera.read()
            if not success:
                print("[WARN] Lost connection to CCTV stream. Reconnecting...")
                self._set_stream_status("reconnecting", "Camera stream lost. Reconnecting...")
                camera = self.connect_to_cctv_stream(stream_source, frame_width, frame_height, target_fps=30)
                if camera is None:
                    self._set_stream_status("disconnected", "Camera stream disconnected.")
                    break
                continue

            frame = cv2.resize(frame, (frame_width, frame_height))
            frame = cv2.flip(frame, 1)
            current_time = time.time()
            self._set_stream_status("live", "Camera stream active and running.", last_frame_ts=current_time)
            if self.state.expire_registration_session_if_needed():
                print("Registration session expired due to inactivity.")
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

            run_detection = self._should_run_detection(frame_index)
            if run_detection:
                results = self.yolo_model.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    conf=0.3,
                    imgsz=768,
                    device=self.yolo_device,
                    verbose=False,
                )

                last_visible_track_ids = []
                last_face_crops = []
                last_face_qualities = []

                for result in results:
                    if result.boxes is None:
                        continue

                    for detection_index, box in enumerate(result.boxes):
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        if (x2 - x1) < self.config.min_face_size or (y2 - y1) < self.config.min_face_size:
                            continue

                        face_crop, clamped_bbox = crop_face_region(frame, x1, y1, x2, y2)
                        if face_crop is None or clamped_bbox is None:
                            continue

                        x1, y1, x2, y2 = clamped_bbox
                        detection_confidence = float(box.conf[0]) if box.conf is not None else None
                        landmarks = self._extract_landmarks_from_result(result, detection_index, clamped_bbox)
                        quality_score, quality_status, quality_debug = self.quality_service.assess_face_quality(
                            face_crop,
                            detection_confidence=detection_confidence,
                            landmarks=landmarks,
                        )
                        detected_pose = self.quality_service.detect_face_pose(face_crop, landmarks=landmarks)

                        track_id = int(box.id[0]) if box.id is not None else None
                        if track_id is None:
                            continue

                        last_visible_track_ids.append(track_id)
                        last_face_crops.append(face_crop)
                        last_face_qualities.append((quality_score, quality_status))

                        track_state = self.tracking_service.initialize_track_state(track_id, current_time)
                        if self.tracking_service.refresh_track_geometry(track_id, (x1, y1, x2, y2)):
                            print(f"[INFO] Track {track_id} geometry changed sharply. Resetting carried identity.")
                            track_state = self.tracking_service.initialize_track_state(track_id, current_time)

                        is_stable = self.tracking_service.check_face_stability(track_id, x1, y1, x2, y2)
                        track_state.last_detection_confidence = detection_confidence
                        track_state.last_quality_score = quality_score
                        track_state.last_quality_status = quality_status
                        track_state.last_quality_debug = quality_debug
                        track_state.last_landmarks = landmarks
                        track_state.last_pose = detected_pose
                        track_state.last_stable = is_stable
                        track_state.last_area = self._bbox_area((x1, y1, x2, y2))
                        track_state.last_analysis_frame_index = frame_index
                        track_state.last_seen = current_time

                self.tracking_service.cleanup_stale_tracks(current_time)
            else:
                self.tracking_service.cleanup_stale_tracks(current_time)

            reg_state = self.state.registration_state
            if run_detection:
                selected_track_id = self._select_registration_candidate(last_visible_track_ids)

                if reg_state.manual_requested and selected_track_id is not None:
                    selected_state = self.state.get_track_state(selected_track_id)
                    from_web_session = bool(reg_state.web_session_active)
                    if selected_state and selected_state.recognized and selected_state.user:
                        print(
                            "Web registration canceled because the selected face is already recognized."
                            if from_web_session
                            else "Manual registration canceled because the selected face is already recognized."
                        )
                        self.state.stop_manual_registration()
                    else:
                        self.state.start_manual_registration(selected_track_id)
                        registration_prompted = False
                        print(
                            f"[INFO] Web registration locked to track {selected_track_id}. Hold still for registration capture."
                            if from_web_session
                            else f"[INFO] Unregistered student locked to track {selected_track_id}. Hold still for registration capture."
                        )

                reg_state = self.state.registration_state
                for track_id in last_visible_track_ids:
                    track_state = self.state.get_track_state(track_id)
                    if track_state is None or not track_state.last_stable or track_state.recognized:
                        continue
                    if reg_state.manual_active and reg_state.manual_track_id is not None and track_id != reg_state.manual_track_id:
                        continue
                    if (current_time - track_state.last_recognition_time) < self.config.recognition_cooldown_seconds:
                        continue

                    bbox = track_state.last_bbox
                    if bbox is None:
                        continue

                    face_crop, clamped_bbox = crop_face_region(frame, *bbox)
                    if face_crop is None or clamped_bbox is None:
                        continue

                    result = self.recognition_service.register_or_recognize_face(
                        face_crop,
                        quality_service=self.quality_service,
                        face_id=track_id,
                        allow_registration=(reg_state.manual_active and track_id == reg_state.manual_track_id),
                        detection_confidence=track_state.last_detection_confidence,
                        landmarks=track_state.last_landmarks,
                        precomputed_quality=(
                            track_state.last_quality_score,
                            track_state.last_quality_status,
                            track_state.last_quality_debug,
                        ),
                    )

                    status = result.get("status")
                    track_state.last_seen = current_time
                    track_state.last_recognition_time = current_time
                    track_state.last_recognition_confidence = result.get("match_confidence")
                    track_state.last_recognition_threshold = result.get("match_threshold")

                    if status == "recognized":
                        track_state.recognized = True
                        track_state.user = dict(self.state.recognized_user) if self.state.recognized_user else None
                        track_state.failed_good_quality_attempts = 0
                    else:
                        track_state.recognized = False
                        track_state.user = None
                        if status in {"uncertain", "no_match"} and track_state.last_quality_score >= self.config.face_quality_threshold:
                            track_state.failed_good_quality_attempts += 1

                    if reg_state.manual_active:
                        if status == "recognized":
                            self.state.stop_manual_registration()
                            self.state.clear_captured_samples()
                            print("Face already exists in the database. First-time registration canceled.")
                        elif self.state.registration_state.in_progress:
                            self.state.stop_manual_registration()

            reg_state = self.state.registration_state
            selected_track_id = reg_state.manual_track_id if reg_state.manual_active else reg_state.selected_track_id

            for track_id in list(last_visible_track_ids):
                track_state = self.state.get_track_state(track_id)
                if track_state is None or track_state.last_bbox is None:
                    continue

                x1, y1, x2, y2 = track_state.last_bbox
                is_selected = track_id == selected_track_id and (reg_state.manual_requested or reg_state.manual_active)
                if is_selected:
                    color = (255, 215, 0)
                    thickness = 4
                elif track_state.recognized and track_state.user:
                    color = (0, 255, 0)
                    thickness = 2
                elif track_state.last_stable:
                    if track_state.last_quality_score >= self.config.face_quality_good_threshold:
                        color = (0, 255, 0)
                    elif track_state.last_quality_score >= self.config.face_quality_threshold:
                        color = (0, 255, 255)
                    else:
                        color = (0, 0, 255)
                    thickness = 2
                else:
                    color = (128, 128, 128)
                    thickness = 1

                label_text, label_color = self._build_identity_label(track_state, reg_state, is_selected)
                track_state.last_label = label_text
                track_state.last_label_color = label_color

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                if is_selected:
                    cv2.putText(
                        frame,
                        "SELECTED",
                        (x1, max(20, y1 - 30)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        color,
                        2,
                    )

                track_text = f"T{track_id}"
                stability_text = "STABLE" if track_state.last_stable else "MOVING"
                issue_text = ""
                if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
                    primary_issue = track_state.last_quality_debug.get("primary_issue_label")
                    if primary_issue and track_state.last_quality_status == "Poor":
                        issue_text = f" {primary_issue}"
                cv2.putText(
                    frame,
                    f"{track_text} {stability_text} Q:{track_state.last_quality_score:.1f}{issue_text}",
                    (x1, max(20, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                )

                label_y = min(y2 + 20, frame_height - 10)
                cv2.putText(
                    frame,
                    label_text,
                    (x1, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    label_color,
                    2,
                )

            reg_state = self.state.registration_state
            if registration_prompted and not reg_state.in_progress:
                registration_prompted = False

            if reg_state.in_progress and reg_state.pending_registration and not registration_prompted:
                registration_prompted = True
                self.handle_registration()
                self.state.set_recognized_user(None)

            for i, (crop, (quality_score, _quality_status)) in enumerate(zip(last_face_crops[:5], last_face_qualities[:5])):
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
            if self.config.cli_top_bar_enabled:
                cv2.rectangle(overlay, (0, 0), (frame_width, 70), (0, 0, 0), -1)
            cv2.rectangle(overlay, (0, frame_height - 50), (frame_width, frame_height), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

            if self.config.cli_top_bar_enabled:
                cv2.putText(
                    frame,
                    "Controls: [D] Debug  [Q] Quit",
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    1,
                )
                cv2.putText(
                    frame,
                    (
                        f"DB Users: {self.state.user_count}   FPS: {current_fps}   "
                        f"Val Frames: {saved_real_val_frames}   "
                        f"Debug: {'ON' if self.config.quality_debug_enabled else 'OFF'}"
                    ),
                    (10, 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (200, 200, 200),
                    1,
                )

            self._draw_registration_guidance(frame, frame_height, selected_track_id)

            if self.state.recognized_user:
                status_text = (
                    f"Recognized: {self.state.recognized_user['name']} "
                    f"({self.state.recognized_user['confidence']})"
                )
                status_color = (0, 255, 0)
            elif reg_state.in_progress:
                status_text = (
                    "Unregistered student detected - captured samples are ready. "
                    "Open the registration page to complete registration."
                )
                status_color = (0, 165, 255)
            elif reg_state.manual_active:
                status_text = (
                    f"Registration capture in progress: {reg_state.capture_count}/{reg_state.max_captures}"
                )
                status_color = (0, 165, 255)
            elif reg_state.manual_requested:
                status_text = (
                    "Web registration session started - hold still for capture"
                    if reg_state.web_session_active
                    else "First-time registration requested - hold still for capture"
                )
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
            if key == ord("d"):
                debug_enabled = self.toggle_quality_debug()
                debug_status = "enabled" if debug_enabled else "disabled"
                print(f"Quality debug {debug_status}.")

        camera.release()
        cv2.destroyAllWindows()
        self._set_stream_status("stopped", "Detection loop stopped.")

    def handle_registration(self):
        reg_state = self.state.registration_state
        pending_registration = reg_state.pending_registration or []
        if not pending_registration:
            print("No pending registration samples.")
            return

        print("\n" + "=" * 50)
        print("REGISTRATION CAPTURE COMPLETE")
        print("=" * 50)
        print(f"Captured {len(pending_registration)} face samples")
        print("This face is not yet registered.")
        print("Open the website registration page and complete the student's first-time registration.")
        print("The pending samples will stay available until the website registration is completed or reset.")

    def main_menu(self):
        print("The website is running at the same time with detection and recognition.")
        print("Use the registration page controls to start and manage first-time registration sessions.")
        self.process_cctv_stream()
