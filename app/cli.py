from __future__ import annotations

import pickle
import subprocess
import sys
import threading
import time
import math
import uuid

import cv2

from core.config import AppConfig
from core.models import User
from core.state import AppStateManager
from database.repository import UserRepository
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
        worker_role: str = "entry",
    ):
        self.config = config
        self.state = state
        self.repository = repository
        self.quality_service = quality_service
        self.recognition_service = recognition_service
        self.tracking_service = tracking_service
        self.yolo_model = yolo_model
        self.yolo_device = yolo_device
        self.worker_role = str(worker_role or "entry").strip().lower()
        self._detection_pause_event = threading.Event()
        self._pause_notice_shown = False
        self._stream_status_lock = threading.Lock()
        self._stream_status = {
            "state": "initializing",
            "message": "Initializing camera stream.",
            "last_frame_ts": None,
            "updated_at": time.time(),
        }
        self._greeting_popup_name = ""
        self._greeting_popup_active_until = 0.0
        self._greeting_popup_duration_seconds = 4.0
        self._greeting_same_user_cooldown_seconds = 8.0
        self._greeting_last_shown_by_user: dict[str, float] = {}

    def _registration_allowed_on_this_worker(self) -> bool:
        return self.worker_role == "entry"

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
        if hasattr(self.state, "set_registration_status_reason"):
            if state == "paused":
                self.state.set_registration_status_reason("detection_paused", message)
            elif state == "disconnected":
                self.state.set_registration_status_reason("stream_disconnected", message)
            elif state == "reconnecting":
                self.state.set_registration_status_reason("stream_reconnecting", message)

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
        keypoints_conf = getattr(result_obj.keypoints, "conf", None)

        try:
            if hasattr(keypoints_xy, "detach"):
                keypoints_xy = keypoints_xy.detach().cpu().numpy()
            else:
                keypoints_xy = keypoints_xy.cpu().numpy() if hasattr(keypoints_xy, "cpu") else keypoints_xy
            if keypoints_conf is not None:
                if hasattr(keypoints_conf, "detach"):
                    keypoints_conf = keypoints_conf.detach().cpu().numpy()
                else:
                    keypoints_conf = keypoints_conf.cpu().numpy() if hasattr(keypoints_conf, "cpu") else keypoints_conf
        except Exception:
            return None

        if detection_index < 0 or detection_index >= len(keypoints_xy):
            return None

        points = keypoints_xy[detection_index]
        if points is None or len(points) < 3:
            return None
        point_conf = None
        if keypoints_conf is not None and detection_index < len(keypoints_conf):
            point_conf = keypoints_conf[detection_index]

        x1, y1, x2, y2 = bbox
        box_w = max(float(x2 - x1), 1.0)
        box_h = max(float(y2 - y1), 1.0)

        def _to_crop_pt(idx):
            if idx >= len(points):
                return None
            if point_conf is not None and idx < len(point_conf):
                kp_conf = float(point_conf[idx])
                if math.isnan(kp_conf) or kp_conf < 0.25:
                    return None
            px, py = points[idx][:2]
            if not math.isfinite(float(px)) or not math.isfinite(float(py)):
                return None
            cx = float(px - x1)
            cy = float(py - y1)
            if cx < (-0.10 * box_w) or cx > (1.10 * box_w):
                return None
            if cy < (-0.10 * box_h) or cy > (1.10 * box_h):
                return None
            return min(max(cx, 0.0), box_w - 1.0), min(max(cy, 0.0), box_h - 1.0)

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

    def _worker_role_label(self) -> str:
        return "ENTRY" if self._registration_allowed_on_this_worker() else "EXIT"

    def _worker_mode_label(self) -> str:
        if self._registration_allowed_on_this_worker():
            return "Rec+Reg"
        return "Rec Only"

    @staticmethod
    def _normalize_display_name(raw_name: object) -> str:
        return str(raw_name or "").strip()

    @staticmethod
    def _truncate_text(value: str, max_chars: int = 30) -> str:
        if len(value) <= max_chars:
            return value
        return f"{value[:max(0, max_chars - 3)].rstrip()}..."

    def _maybe_trigger_greeting_popup(self, recognized_user: dict[str, str] | None, now: float | None = None) -> None:
        if not self._registration_allowed_on_this_worker():
            return
        user_name = self._normalize_display_name((recognized_user or {}).get("name"))
        if not user_name:
            return

        observed_now = float(now if now is not None else time.time())
        cooldown = max(0.0, float(self._greeting_same_user_cooldown_seconds))
        duration = max(0.5, float(self._greeting_popup_duration_seconds))
        normalized_key = user_name.casefold()

        last_shown_at = self._greeting_last_shown_by_user.get(normalized_key)
        if last_shown_at is not None and (observed_now - float(last_shown_at)) < cooldown:
            return

        stale_cutoff = observed_now - max(cooldown, duration) * 3.0
        stale_keys = [
            key for key, shown_at in self._greeting_last_shown_by_user.items()
            if float(shown_at) < stale_cutoff
        ]
        for key in stale_keys:
            self._greeting_last_shown_by_user.pop(key, None)

        self._greeting_last_shown_by_user[normalized_key] = observed_now
        self._greeting_popup_name = user_name
        self._greeting_popup_active_until = observed_now + duration

    def _draw_greeting_popup(self, frame, frame_width: int, frame_height: int, now: float | None = None) -> None:
        if not self._registration_allowed_on_this_worker():
            return
        if not self._greeting_popup_name:
            return
        observed_now = float(now if now is not None else time.time())
        if observed_now >= float(self._greeting_popup_active_until):
            self._greeting_popup_name = ""
            return

        popup_width = min(430, max(290, int(frame_width * 0.34)))
        popup_height = 116
        margin_right = 18
        x2 = frame_width - margin_right
        x1 = max(12, x2 - popup_width)
        y1 = max(90, int(frame_height * 0.22))
        y2 = min(frame_height - 110, y1 + popup_height)
        if y2 <= y1:
            return

        overlay = frame.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (20, 65, 34), -1)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (72, 220, 120), 2)
        cv2.addWeighted(overlay, 0.74, frame, 0.26, 0, frame)

        cv2.putText(
            frame,
            "Welcome",
            (x1 + 18, y1 + 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (210, 255, 224),
            2,
        )
        cv2.putText(
            frame,
            self._truncate_text(f"{self._greeting_popup_name}!"),
            (x1 + 18, y1 + 82),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.86,
            (255, 255, 255),
            2,
        )

    def _build_footer_guidance(self, reg_state) -> tuple[str, str, tuple[int, int, int]]:
        recognized_user = self.state.recognized_user or {}
        if recognized_user:
            user_name = str(recognized_user.get("name") or "Unknown user").strip() or "Unknown user"
            status_line = f"Recognized: {user_name}"
            return status_line, "Pass", (0, 255, 0)

        if not self._registration_allowed_on_this_worker():
            return (
                "Exit scan active",
                "Center face",
                (255, 255, 255),
            )

        if reg_state.in_progress:
            return (
                "Capture complete",
                "Open /register",
                (0, 165, 255),
            )

        if reg_state.manual_active:
            expected_pose = self.state.get_current_registration_pose() or "front"
            pose_label = self._pose_instruction_text(expected_pose)
            return (
                f"Capturing {reg_state.capture_count}/{reg_state.max_captures}",
                f"Pose: {pose_label}",
                (0, 165, 255),
            )

        if reg_state.manual_requested:
            return (
                "Waiting for lock",
                "One face in frame",
                (0, 165, 255),
            )

        if reg_state.phase == "expired":
            return (
                "Session expired",
                "Restart in /register",
                (0, 165, 255),
            )

        return "Scanning...", "Center face", (255, 255, 255)

    def _build_identity_label(self, track_state, reg_state, is_selected_for_registration: bool):
        if track_state is None:
            return "No track", (180, 180, 180)

        if track_state.recognized and track_state.user:
            label = f"Recognized: {track_state.user['name']}"
            return label, (0, 255, 0)

        if is_selected_for_registration and reg_state.manual_active:
            if track_state.last_quality_score < self.config.face_quality_threshold:
                return "Target: Improve quality", (0, 140, 255)
            expected_pose = self.state.get_current_registration_pose()
            if expected_pose and track_state.last_pose and track_state.last_pose != expected_pose:
                expected_pose_text = self._pose_instruction_text(expected_pose)
                return f"Target: {expected_pose_text}", (0, 165, 255)
            return "Target: Hold still", (255, 215, 0)

        if is_selected_for_registration and reg_state.manual_requested:
            return "Candidate", (255, 215, 0)

        if track_state.last_recognition_confidence is not None:
            threshold = track_state.last_recognition_threshold or self.config.recognition_confidence_threshold
            if track_state.last_recognition_confidence < threshold:
                return "Uncertain", (0, 215, 255)

        if track_state.failed_good_quality_attempts >= self.config.unknown_person_attempt_threshold:
            return "No match", (0, 165, 255)

        if track_state.last_quality_score < self.config.face_quality_threshold:
            return "Low quality", (0, 140, 255)

        return "Detected", (180, 180, 180)

    def _is_registration_lock_candidate(self, track_state) -> bool:
        if track_state is None:
            return False
        if not track_state.last_stable:
            return False
        if track_state.recognized:
            return False
        min_area = int(self.config.registration_min_face_area)
        return track_state.last_area >= min_area

    def _select_largest_registration_candidate(self, candidate_ids: list[int]) -> int | None:
        selected_id = None
        largest_area = -1
        for track_id in candidate_ids:
            track_state = self.state.get_track_state(track_id)
            if track_state is None:
                continue
            area = int(track_state.last_area or 0)
            if area > largest_area:
                largest_area = area
                selected_id = track_id
        return selected_id

    def _select_registration_candidate(self, visible_track_ids: list[int]):
        reg_state = self.state.registration_state
        for _track_id, track_state in self.state.tracked_faces.items():
            track_state.selected_for_registration = False

        if reg_state.manual_active and reg_state.manual_track_id is not None:
            selected_id = reg_state.manual_track_id
        else:
            eligible_ids = [
                track_id
                for track_id in visible_track_ids
                if self._is_registration_lock_candidate(self.state.get_track_state(track_id))
            ]
            locked_id = reg_state.selected_track_id
            if locked_id in eligible_ids:
                selected_id = locked_id
            else:
                selected_id = self._select_largest_registration_candidate(eligible_ids)

        reg_state.selected_track_id = selected_id
        if selected_id is not None:
            selected_state = self.state.get_track_state(selected_id)
            if selected_state is not None:
                selected_state.selected_for_registration = True
        return selected_id

    def _locked_registration_track_id(self) -> int | None:
        reg_state = self.state.registration_state
        if reg_state.manual_active:
            return reg_state.manual_track_id
        return reg_state.selected_track_id

    @staticmethod
    def _should_skip_track_during_registration(
        registration_enabled: bool,
        reg_state,
        locked_track_id: int | None,
        track_id: int,
    ) -> bool:
        if not registration_enabled:
            return False
        if not reg_state.manual_active:
            return False
        if locked_track_id is None:
            return False
        return track_id != locked_track_id

    @staticmethod
    def _reset_registration_recognition_streak(track_state) -> None:
        track_state.registration_recognized_streak = 0
        track_state.registration_recognized_name = ""

    def _handle_existing_recognition_during_registration(self, track_state, result: dict) -> None:
        reg_state = self.state.registration_state
        if not reg_state.manual_active:
            return

        if reg_state.allow_unknown_override:
            self._reset_registration_recognition_streak(track_state)
            if hasattr(self.state, "set_registration_status_reason"):
                self.state.set_registration_status_reason(
                    "override_forced_unknown",
                    "Manual override enabled. Continuing registration as an unknown student.",
                )
            return

        user_name = (track_state.user or {}).get("name", "").strip() or "an existing user"
        confidence = _coerce_float(result.get("match_confidence")) or 0.0
        threshold = _coerce_float(result.get("match_threshold")) or float(
            self.config.recognition_confidence_threshold
        )
        required_frames = max(
            1,
            int(getattr(self.config, "registration_recognition_confirm_frames", 5)),
        )

        if confidence < threshold:
            track_state.registration_recognized_streak = 0
            track_state.registration_recognized_name = user_name
            if hasattr(self.state, "set_registration_status_reason"):
                self.state.set_registration_status_reason(
                    "possible_existing_match",
                    (
                        f"Possible match with {user_name}. "
                        "Hold still to confirm or use Continue as New Student."
                    ),
                )
            return

        if track_state.registration_recognized_name == user_name:
            track_state.registration_recognized_streak += 1
        else:
            track_state.registration_recognized_name = user_name
            track_state.registration_recognized_streak = 1

        if track_state.registration_recognized_streak < required_frames:
            if hasattr(self.state, "set_registration_status_reason"):
                self.state.set_registration_status_reason(
                    "possible_existing_match",
                    (
                        f"Possible match with {user_name} "
                        f"[{track_state.registration_recognized_streak}/{required_frames} confirmations]. "
                        "Hold still to confirm or use Continue as New Student."
                    ),
                )
            return

        self.state.stop_manual_registration()
        self.state.clear_captured_samples()
        self._reset_registration_recognition_streak(track_state)
        if hasattr(self.state, "set_registration_status_reason"):
            self.state.set_registration_status_reason(
                "recognized_existing",
                f"Face confirmed as {user_name}. First-time registration was canceled.",
            )
        print(f"Face confirmed as {user_name}. First-time registration canceled.")

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
        if not self._registration_allowed_on_this_worker():
            return

        reg_state = self.state.registration_state
        if not (reg_state.manual_requested or reg_state.manual_active):
            return

        lines = []
        if reg_state.manual_active and selected_track_id is not None:
            track_state = self.state.get_track_state(selected_track_id)
            if track_state is None:
                return

            expected_pose = self.state.get_current_registration_pose() or "front"
            lines.append(f"T{selected_track_id} | {self._pose_instruction_text(expected_pose)}")
            lines.append(f"{reg_state.capture_count}/{reg_state.max_captures}")

            distance_feedback = self._distance_feedback(track_state.last_area)
            if distance_feedback:
                lines.append(distance_feedback)
        else:
            lines = [
                "Registration active",
                "Waiting for lock",
            ]

        panel_top = max(90, frame_height - 150)
        self._draw_text_block(frame, lines, 10, panel_top, (0, 220, 255), scale=0.6, thickness=2, line_gap=22)

    def process_cctv_stream(self, stream_source=None, frame_width=1280, frame_height=720, window_title: str | None = None):
        if stream_source is None:
            stream_source = 0
        display_title = window_title or "CCTV Face Recognition"

        camera = self.connect_to_cctv_stream(stream_source, frame_width, frame_height, target_fps=30)
        if camera is None:
            self._set_stream_status("disconnected", "Camera stream is unavailable.")
            return

        print("\n" + "=" * 50)
        print("CCTV FACE RECOGNITION SYSTEM")
        print("=" * 50)
        worker_mode = "recognition + registration capture" if self._registration_allowed_on_this_worker() else "recognition only"
        print(f"Worker role: {self._worker_role_label()} ({worker_mode})")
        print("Press 'q' to quit")
        print("Press 'd' to toggle quality debug")
        if not self._registration_allowed_on_this_worker():
            print("Registration capture is available on the ENTRY worker only.")
        print("=" * 50)

        fps_counter = 0
        fps_start_time = time.time()
        current_fps = 0
        frame_index = 0
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

            if self.state.consume_tracking_refresh_request():
                last_visible_track_ids = []
                last_face_crops = []
                last_face_qualities = []
                registration_prompted = False

            run_detection = self._should_run_detection(frame_index)
            if run_detection:
                results = self.yolo_model.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    conf=float(self.config.yolo_detection_confidence),
                    imgsz=int(self.config.yolo_inference_imgsz),
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

            registration_enabled = self._registration_allowed_on_this_worker()
            reg_state = self.state.registration_state
            if run_detection:
                selected_track_id = self._select_registration_candidate(last_visible_track_ids) if registration_enabled else None

                if registration_enabled and reg_state.manual_requested and selected_track_id is not None:
                    selected_state = self.state.get_track_state(selected_track_id)
                    from_web_session = bool(reg_state.web_session_active)
                    if selected_state and selected_state.recognized and selected_state.user:
                        if hasattr(self.state, "set_registration_status_reason"):
                            self.state.set_registration_status_reason(
                                "already_recognized",
                                "Selected face is already recognized. Registration capture was canceled.",
                            )
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
                locked_track_id = self._locked_registration_track_id()
                for track_id in last_visible_track_ids:
                    track_state = self.state.get_track_state(track_id)
                    if track_state is None or not track_state.last_stable or track_state.recognized:
                        continue
                    if self._should_skip_track_during_registration(
                        registration_enabled=registration_enabled,
                        reg_state=reg_state,
                        locked_track_id=locked_track_id,
                        track_id=track_id,
                    ):
                        continue
                    if (current_time - track_state.last_recognition_time) < self.config.recognition_cooldown_seconds:
                        continue

                    bbox = track_state.last_bbox
                    if bbox is None:
                        continue

                    face_crop, clamped_bbox = crop_face_region(frame, *bbox)
                    if face_crop is None or clamped_bbox is None:
                        continue

                    registration_capture_allowed = (
                        registration_enabled
                        and reg_state.manual_active
                        and track_id == locked_track_id
                    )
                    result = self.recognition_service.register_or_recognize_face(
                        face_crop,
                        quality_service=self.quality_service,
                        allow_registration=registration_capture_allowed,
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
                    local_capture_count = None
                    if registration_capture_allowed and status == "registration_captured":
                        registration_sample = result.get("registration_sample")
                        if registration_sample is not None:
                            local_capture_count = int(self.state.capture_registration_sample(registration_sample))

                    if status == "recognized":
                        track_state.recognized = True
                        track_state.user = dict(self.state.recognized_user) if self.state.recognized_user else None
                        track_state.failed_good_quality_attempts = 0
                        self._maybe_trigger_greeting_popup(track_state.user, now=current_time)
                    else:
                        track_state.recognized = False
                        track_state.user = None
                        self._reset_registration_recognition_streak(track_state)
                        if status in {"uncertain", "no_match"} and track_state.last_quality_score >= self.config.face_quality_threshold:
                            track_state.failed_good_quality_attempts += 1

                    if registration_capture_allowed:
                        if status == "recognized":
                            self._handle_existing_recognition_during_registration(track_state, result)
                        elif self.state.registration_state.in_progress:
                            self.state.stop_manual_registration()
                        elif hasattr(self.state, "set_registration_status_reason"):
                            if status == "pose_mismatch":
                                expected_pose = result.get("expected_pose") or "front"
                                detected_pose = result.get("detected_pose") or "unknown"
                                self.state.set_registration_status_reason(
                                    "pose_mismatch",
                                    f"Pose mismatch: expected {expected_pose}, detected {detected_pose}.",
                                )
                            elif status == "low_quality":
                                quality_debug = result.get("quality_debug") or {}
                                issue_label = (quality_debug.get("primary_issue_label") or "low quality").strip()
                                self.state.set_registration_status_reason(
                                    "low_quality",
                                    f"Capture quality too low ({issue_label}). Keep face centered and well lit.",
                                )
                            elif status == "registration_captured":
                                capture_count = (
                                    int(local_capture_count)
                                    if local_capture_count is not None
                                    else int(self.state.registration_state.capture_count)
                                )
                                max_captures = self.state.registration_state.max_captures
                                self.state.set_registration_status_reason(
                                    "capture_in_progress",
                                    f"Captured {capture_count}/{max_captures}.",
                                )
                            elif status == "uncertain":
                                self.state.set_registration_status_reason(
                                    "uncertain_match",
                                    "Face match is uncertain. Keep still and improve lighting for clearer capture.",
                                )
                            elif status == "no_match":
                                self.state.set_registration_status_reason(
                                    "no_match",
                                    "No match yet. Continue holding position for registration capture.",
                                )
                    if (
                        status == "registration_captured"
                        and registration_enabled
                        and hasattr(self.repository, "enqueue_registration_sample")
                    ):
                        session_id = str(getattr(self.state.registration_state, "session_id", "") or "").strip()
                        registration_sample = result.get("registration_sample")
                        if session_id and registration_sample is not None:
                            try:
                                self.repository.enqueue_registration_sample(
                                    sample_id=f"sample-{uuid.uuid4().hex}",
                                    session_id=session_id,
                                    pose=str(getattr(registration_sample, "pose", "front") or "front"),
                                    quality=float(getattr(registration_sample, "quality", 0.0) or 0.0),
                                    face_crop=getattr(registration_sample, "face_crop", None),
                                    embeddings=getattr(registration_sample, "embeddings", {}) or {},
                                )
                            except Exception as exc:
                                print(f"[WARN] Failed to queue registration sample for API sync: {exc}")

            reg_state = self.state.registration_state
            selected_track_id = self._locked_registration_track_id() if registration_enabled else None

            for track_id in list(last_visible_track_ids):
                track_state = self.state.get_track_state(track_id)
                if track_state is None or track_state.last_bbox is None:
                    continue

                x1, y1, x2, y2 = track_state.last_bbox
                is_selected = (
                    registration_enabled
                    and track_id == selected_track_id
                    and (reg_state.manual_requested or reg_state.manual_active)
                )
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
                        "REG TARGET",
                        (x1, max(20, y1 - 30)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        color,
                        2,
                    )

                track_text = f"T{track_id}"
                stability_text = "Stable" if track_state.last_stable else "Moving"
                issue_text = ""
                if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
                    primary_issue = track_state.last_quality_debug.get("primary_issue_label")
                    if primary_issue and track_state.last_quality_status == "Poor":
                        issue_text = f" {primary_issue}"
                cv2.putText(
                    frame,
                    f"{track_text} | {stability_text} | Q:{track_state.last_quality_score:.1f}{issue_text}",
                    (x1, max(20, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                )

                label_y = min(y2 + 20, frame_height - 90)
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
            footer_height = 82
            cv2.rectangle(overlay, (0, frame_height - footer_height), (frame_width, frame_height), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

            if self.config.cli_top_bar_enabled:
                stream_state = str(self.get_stream_status().get("state") or "unknown").upper()
                cv2.putText(
                    frame,
                    (
                        f"{self._worker_role_label()} | {self._worker_mode_label()} | D Debug | Q Quit"
                    ),
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    1,
                )
                cv2.putText(
                    frame,
                    (
                        f"Users: {self.state.user_count}   FPS: {current_fps}   "
                        f"Stream: {stream_state}   "
                        f"Debug: {'ON' if self.config.quality_debug_enabled else 'OFF'}"
                    ),
                    (10, 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (200, 200, 200),
                    1,
                )

            self._draw_registration_guidance(frame, frame_height, selected_track_id)

            status_text, next_step_text, status_color = self._build_footer_guidance(reg_state)

            cv2.putText(
                frame,
                status_text,
                (10, frame_height - 46),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                status_color,
                2,
            )
            cv2.putText(
                frame,
                next_step_text,
                (10, frame_height - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.57,
                (230, 230, 230),
                1,
            )
            self._draw_greeting_popup(frame, frame_width, frame_height, now=current_time)

            cv2.imshow(display_title, frame)

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
