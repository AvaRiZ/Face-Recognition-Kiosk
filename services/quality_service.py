from __future__ import annotations

import cv2
import numpy as np
from typing import Optional

from core.config import AppConfig


def _clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _score_higher_better(value, low_threshold, high_threshold):
    if high_threshold <= low_threshold:
        return 1.0 if value >= high_threshold else 0.0
    return _clamp01((float(value) - low_threshold) / (high_threshold - low_threshold))


def _score_lower_better(value, good_threshold, bad_threshold):
    if bad_threshold <= good_threshold:
        return 1.0 if value <= good_threshold else 0.0
    return _clamp01(1.0 - ((float(value) - good_threshold) / (bad_threshold - good_threshold)))


def _to_grayscale(face_crop):
    if len(face_crop.shape) == 3:
        return cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    return face_crop


def _normalize_landmarks(landmarks):
    if landmarks is None:
        return None

    normalized = {}

    def _to_point(value):
        if value is None:
            return None
        arr = np.asarray(value, dtype=np.float32).reshape(-1)
        if arr.shape[0] < 2:
            return None
        return float(arr[0]), float(arr[1])

    if isinstance(landmarks, dict):
        for key in ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right", "mouth"):
            pt = _to_point(landmarks.get(key))
            if pt is not None:
                normalized[key] = pt
    else:
        pts = np.asarray(landmarks, dtype=np.float32)
        if pts.ndim == 2 and pts.shape[1] >= 2:
            if pts.shape[0] >= 1:
                normalized["left_eye"] = (float(pts[0, 0]), float(pts[0, 1]))
            if pts.shape[0] >= 2:
                normalized["right_eye"] = (float(pts[1, 0]), float(pts[1, 1]))
            if pts.shape[0] >= 3:
                normalized["nose"] = (float(pts[2, 0]), float(pts[2, 1]))
            if pts.shape[0] >= 4:
                normalized["mouth_left"] = (float(pts[3, 0]), float(pts[3, 1]))
            if pts.shape[0] >= 5:
                normalized["mouth_right"] = (float(pts[4, 0]), float(pts[4, 1]))

    if "mouth" not in normalized:
        ml = normalized.get("mouth_left")
        mr = normalized.get("mouth_right")
        if ml is not None and mr is not None:
            normalized["mouth"] = ((ml[0] + mr[0]) * 0.5, (ml[1] + mr[1]) * 0.5)

    return normalized or None


class FaceQualityService:
    def __init__(self, config: AppConfig):
        self.config = config

    def classify_face_pose(self, landmarks, width: int, height: int) -> Optional[str]:
        normalized = _normalize_landmarks(landmarks)
        if normalized is None:
            return None

        left_eye = normalized.get("left_eye")
        right_eye = normalized.get("right_eye")
        nose = normalized.get("nose")
        if left_eye is None or right_eye is None or nose is None:
            return None
        eye_span = abs(float(right_eye[0]) - float(left_eye[0]))
        if eye_span < max(float(width) * 0.08, 8.0):
            return None

        eye_mid_x = (left_eye[0] + right_eye[0]) * 0.5
        half_eye_span = max(eye_span * 0.5, 1.0)
        signed_yaw_ratio = float((nose[0] - eye_mid_x) / half_eye_span)
        abs_yaw_ratio = abs(signed_yaw_ratio)

        if abs_yaw_ratio <= self.config.registration_pose_front_max_yaw_ratio:
            return "front"
        if abs_yaw_ratio < self.config.registration_pose_side_min_yaw_ratio:
            return None
        # Signed yaw is measured in image coordinates.
        return "left" if signed_yaw_ratio < 0 else "right"

    def detect_face_pose(self, face_crop, landmarks=None) -> Optional[str]:
        h, w = (face_crop.shape[:2] if face_crop is not None and getattr(face_crop, "size", 0) != 0 else (0, 0))
        if h <= 0 or w <= 0:
            return None
        return self.classify_face_pose(landmarks, w, h)

    @staticmethod
    def _describe_check(check_name):
        labels = {
            "empty_face": "empty crop",
            "size": "face too small",
            "detection_confidence": "low detector confidence",
            "sharpness": "too blurry",
            "exposure": "bad brightness",
            "dynamic_range": "low contrast",
            "alignment": "tilted face",
            "pose": "face turned away",
            "truncation": "face cut off",
            "landmarks": "face landmarks unavailable",
            "landmarks_unavailable": "face landmarks not detected",
            "landmarks_unreliable": "unstable face landmarks",
        }
        return labels.get(check_name, check_name.replace("_", " "))

    @staticmethod
    def _format_component_scores(component_scores):
        ordered_items = [
            ("size", component_scores.get("size_score", 0.0)),
            ("sharpness", component_scores.get("sharpness_score", 0.0)),
            ("detection", component_scores.get("detection_score", 0.0)),
            ("exposure", component_scores.get("exposure_score", 0.0)),
            ("pose", component_scores.get("pose_score", 0.0)),
            ("truncation", component_scores.get("occlusion_score", 0.0)),
        ]
        return " | ".join(f"{name}={value:.2f}" for name, value in ordered_items)

    def primary_quality_issue(self, debug_info):
        failed_checks = debug_info.get("failed_checks", [])
        if failed_checks:
            primary_check = failed_checks[0]
            return primary_check, self._describe_check(primary_check)

        component_scores = debug_info.get("component_scores", {})
        if not component_scores:
            return None, "no issue detected"

        aliases = {
            "size_score": "size",
            "sharpness_score": "sharpness",
            "detection_score": "detection_confidence",
            "exposure_score": "exposure",
            "pose_score": "pose",
            "occlusion_score": "truncation",
        }
        weakest_name, weakest_score = min(component_scores.items(), key=lambda item: item[1])
        quality_degraded_reason = debug_info.get("quality_degraded_reason")
        if (
            quality_degraded_reason in {"landmarks_unavailable", "landmarks_unreliable"}
            and weakest_name in {"pose_score", "occlusion_score"}
        ):
            return quality_degraded_reason, self._describe_check(quality_degraded_reason)
        issue_name = aliases.get(weakest_name, weakest_name)
        return issue_name, f"{self._describe_check(issue_name)} ({weakest_score:.2f})"

    def quality_debug_summary(self, debug_info):
        issue_name, issue_label = self.primary_quality_issue(debug_info)
        component_summary = self._format_component_scores(debug_info.get("component_scores", {}))
        raw_metrics = (
            f"sharpness={debug_info.get('sharpness', 0.0):.1f} | "
            f"brightness={debug_info.get('brightness', 0.0):.1f} | "
            f"dynamic_range={debug_info.get('dynamic_range', 0.0):.1f} | "
            f"det={debug_info.get('detection_confidence', 0.0):.2f}"
        )
        if issue_name is None:
            return f"{component_summary} | {raw_metrics}"
        return f"main_issue={issue_label} | {component_summary} | {raw_metrics}"

    def _alignment_pose_from_landmarks(self, landmarks, width, height):
        left_eye = landmarks.get("left_eye")
        right_eye = landmarks.get("right_eye")
        nose = landmarks.get("nose")
        mouth = landmarks.get("mouth")

        if left_eye is None or right_eye is None:
            return 0.5, 0.5, 0.5

        eye_dx = right_eye[0] - left_eye[0]
        eye_dy = right_eye[1] - left_eye[1]
        eye_dist = max(np.hypot(eye_dx, eye_dy), 1.0)

        eye_tilt_ratio = abs(eye_dy) / eye_dist
        alignment_score = _score_lower_better(
            eye_tilt_ratio,
            self.config.quality_pose_eye_tilt_good,
            self.config.quality_pose_eye_tilt_max,
        )

        pose_score = 0.5
        eye_mid_x = (left_eye[0] + right_eye[0]) * 0.5
        eye_span = abs(float(right_eye[0]) - float(left_eye[0]))
        half_eye_span = max(eye_span * 0.5, 1.0)
        if nose is not None and eye_span >= max(float(width) * 0.08, 8.0):
            yaw_ratio = abs(nose[0] - eye_mid_x) / half_eye_span
            pose_score = _score_lower_better(
                yaw_ratio,
                self.config.quality_pose_yaw_good,
                self.config.quality_pose_yaw_max,
            )

        points = [pt for pt in (left_eye, right_eye, nose, mouth) if pt is not None]
        margin_ratio = 0.0
        if points:
            margin_x = min(min(pt[0] for pt in points), width - max(pt[0] for pt in points))
            margin_y = min(min(pt[1] for pt in points), height - max(pt[1] for pt in points))
            margin_ratio = max(0.0, min(margin_x / max(width, 1.0), margin_y / max(height, 1.0)))

        truncation_score = _score_higher_better(
            margin_ratio,
            self.config.quality_landmark_margin_min,
            self.config.quality_landmark_margin_good,
        )
        return _clamp01(alignment_score), _clamp01(pose_score), _clamp01(truncation_score)

    def _compute_exposure_metrics(self, gray):
        gray_f = gray.astype(np.float32, copy=False)
        brightness = float(np.mean(gray_f))
        p5, p95 = np.percentile(gray_f, [5, 95])
        dynamic_range = float(p95 - p5)
        contrast = float(np.std(gray_f))

        if brightness < self.config.quality_brightness_good_min:
            brightness_score = _score_higher_better(
                brightness,
                self.config.quality_brightness_min,
                self.config.quality_brightness_good_min,
            )
        elif brightness > self.config.quality_brightness_good_max:
            brightness_score = _score_lower_better(
                brightness,
                self.config.quality_brightness_good_max,
                self.config.quality_brightness_max,
            )
        else:
            brightness_score = 1.0

        range_score = _score_higher_better(
            dynamic_range,
            self.config.quality_dynamic_range_min,
            self.config.quality_dynamic_range_good,
        )
        exposure_score = _clamp01((brightness_score + range_score) * 0.5)

        return {
            "brightness": brightness,
            "contrast": contrast,
            "dynamic_range": dynamic_range,
            "exposure_score": exposure_score,
            "brightness_score": brightness_score,
        }

    def assess_face_quality(self, face_crop, detection_confidence=None, landmarks=None):
        if face_crop is None or face_crop.size == 0:
            debug_info = {
                "sharpness": 0.0,
                "brightness": 0.0,
                "contrast": 0.0,
                "alignment_score": 0.0,
                "detection_confidence": float(detection_confidence) if detection_confidence is not None else 0.0,
                "pose_score": 0.0,
                "size_score": 0.0,
                "sharpness_score": 0.0,
                "detection_score": 0.0,
                "exposure_score": 0.0,
                "occlusion_score": 0.0,
                "failed_checks": ["empty_face"],
                "component_scores": {
                    "size_score": 0.0,
                    "sharpness_score": 0.0,
                    "detection_score": 0.0,
                    "exposure_score": 0.0,
                    "pose_score": 0.0,
                    "occlusion_score": 0.0,
                },
            }
            issue_name, issue_label = self.primary_quality_issue(debug_info)
            debug_info["primary_issue"] = issue_name
            debug_info["primary_issue_label"] = issue_label
            return 0.0, "Poor", debug_info

        h, w = face_crop.shape[:2]
        area = h * w
        gray = _to_grayscale(face_crop)

        size_score = _score_higher_better(
            area,
            self.config.quality_face_area_min,
            self.config.quality_face_area_good,
        )

        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharpness_score = _score_higher_better(
            laplacian_var,
            self.config.quality_sharpness_min,
            self.config.quality_sharpness_good,
        )

        confidence_value = float(detection_confidence) if detection_confidence is not None else 0.5
        confidence_value = _clamp01(confidence_value)
        detection_score = _score_higher_better(
            confidence_value,
            self.config.quality_detection_confidence_min,
            self.config.quality_detection_confidence_good,
        )

        exposure_metrics = self._compute_exposure_metrics(gray)
        normalized_landmarks = _normalize_landmarks(landmarks)
        quality_degraded_reason = None
        left_eye = normalized_landmarks.get("left_eye") if normalized_landmarks else None
        right_eye = normalized_landmarks.get("right_eye") if normalized_landmarks else None
        landmarks_reliable = (
            left_eye is not None
            and right_eye is not None
            and abs(float(right_eye[0]) - float(left_eye[0])) >= max(float(w) * 0.08, 8.0)
        )

        if landmarks_reliable:
            alignment_score, pose_score, occlusion_score = self._alignment_pose_from_landmarks(
                normalized_landmarks,
                w,
                h,
            )
            alignment_source = "landmarks"
        else:
            alignment_score = 0.0
            pose_score = 0.0
            occlusion_score = 0.0
            alignment_source = "unavailable"
            if normalized_landmarks is None:
                quality_degraded_reason = "landmarks_unavailable"
            else:
                quality_degraded_reason = "landmarks_unreliable"

        component_scores = [
            size_score,
            sharpness_score,
            detection_score,
            exposure_metrics["exposure_score"],
            pose_score,
            occlusion_score,
        ]
        quality_score = _clamp01(float(np.mean(component_scores)))

        failed_checks = []
        if area < self.config.quality_face_area_min:
            failed_checks.append("size")
        if detection_confidence is not None and confidence_value < self.config.quality_detection_confidence_min:
            failed_checks.append("detection_confidence")
        if laplacian_var < self.config.quality_sharpness_min:
            failed_checks.append("sharpness")
        if not (self.config.quality_brightness_min <= exposure_metrics["brightness"] <= self.config.quality_brightness_max):
            failed_checks.append("exposure")
        if exposure_metrics["dynamic_range"] < self.config.quality_dynamic_range_min:
            failed_checks.append("dynamic_range")
        if landmarks_reliable:
            if alignment_score < 0.4:
                failed_checks.append("alignment")
            if pose_score < 0.4:
                failed_checks.append("pose")
            if occlusion_score < 0.4:
                failed_checks.append("truncation")
        else:
            failed_checks.append("landmarks")

        if failed_checks:
            quality_score = min(quality_score, max(0.0, self.config.face_quality_threshold - 0.01))
            quality_status = "Poor"
        elif quality_score >= self.config.face_quality_good_threshold:
            quality_status = "Good"
        elif quality_score >= self.config.face_quality_threshold:
            quality_status = "Acceptable"
        else:
            quality_status = "Poor"

        debug_info = {
            "sharpness": laplacian_var,
            "brightness": exposure_metrics["brightness"],
            "contrast": exposure_metrics["contrast"],
            "alignment_score": alignment_score,
            "detection_confidence": confidence_value,
            "pose_score": pose_score,
            "size_score": size_score,
            "sharpness_score": sharpness_score,
            "exposure_score": exposure_metrics["exposure_score"],
            "brightness_score": exposure_metrics["brightness_score"],
            "dynamic_range": exposure_metrics["dynamic_range"],
            "occlusion_score": occlusion_score,
            "alignment_source": alignment_source,
            "landmarks_available": normalized_landmarks is not None,
            "landmarks_reliable": landmarks_reliable,
            "quality_degraded_reason": quality_degraded_reason,
            "failed_checks": failed_checks,
            "component_scores": {
                "size_score": size_score,
                "sharpness_score": sharpness_score,
                "detection_score": detection_score,
                "exposure_score": exposure_metrics["exposure_score"],
                "pose_score": pose_score,
                "occlusion_score": occlusion_score,
            },
        }
        issue_name, issue_label = self.primary_quality_issue(debug_info)
        debug_info["primary_issue"] = issue_name
        debug_info["primary_issue_label"] = issue_label

        return quality_score, quality_status, debug_info
