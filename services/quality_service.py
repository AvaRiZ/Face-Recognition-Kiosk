from __future__ import annotations

import cv2
import numpy as np

from core.config import AppConfig


def _clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _three_level_score(value, low_threshold, high_threshold):
    value = float(value)
    if value < low_threshold:
        return 0.0
    if value < high_threshold:
        return 0.5
    return 1.0


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

    def _alignment_pose_from_landmarks(self, landmarks):
        left_eye = landmarks.get("left_eye")
        right_eye = landmarks.get("right_eye")
        nose = landmarks.get("nose")
        mouth = landmarks.get("mouth")

        if left_eye is None or right_eye is None:
            return 0.5, 0.5

        eye_dx = right_eye[0] - left_eye[0]
        eye_dy = right_eye[1] - left_eye[1]
        eye_dist = max(np.hypot(eye_dx, eye_dy), 1.0)

        eye_tilt_ratio = abs(eye_dy) / eye_dist
        eye_alignment_score = _score_lower_better(
            eye_tilt_ratio,
            self.config.quality_eye_tilt_good_ratio,
            self.config.quality_eye_tilt_bad_ratio,
        )

        vertical_order_score = 0.5
        if nose is not None and mouth is not None:
            eyes_top = max(left_eye[1], right_eye[1])
            vertical_order_score = 1.0 if (eyes_top < nose[1] < mouth[1]) else 0.2
        elif nose is not None:
            eyes_top = max(left_eye[1], right_eye[1])
            vertical_order_score = 1.0 if eyes_top < nose[1] else 0.3

        alignment_score = _clamp01(0.75 * eye_alignment_score + 0.25 * vertical_order_score)

        pose_score = 0.5
        eye_mid_x = (left_eye[0] + right_eye[0]) * 0.5
        half_eye_span = max(abs(right_eye[0] - left_eye[0]) * 0.5, 1.0)
        if nose is not None:
            yaw_ratio = abs(nose[0] - eye_mid_x) / half_eye_span
            pose_score = _score_lower_better(
                yaw_ratio,
                self.config.quality_pose_good_ratio,
                self.config.quality_pose_bad_ratio,
            )
        elif mouth is not None:
            yaw_ratio = abs(mouth[0] - eye_mid_x) / half_eye_span
            pose_score = _score_lower_better(
                yaw_ratio,
                self.config.quality_pose_good_ratio,
                self.config.quality_pose_bad_ratio,
            )

        return alignment_score, _clamp01(pose_score)

    @staticmethod
    def _band_center_x(binary_band):
        ys, xs = np.where(binary_band > 0)
        if xs.size == 0:
            return None
        return float(np.mean(xs))

    def _approximate_alignment_pose(self, edges):
        h, w = edges.shape[:2]
        if h < 20 or w < 20:
            return 0.5, 0.5

        bands = ((0.18, 0.42), (0.40, 0.64), (0.62, 0.88))
        centers = []
        for y_start_ratio, y_end_ratio in bands:
            y1 = max(0, int(h * y_start_ratio))
            y2 = min(h, int(h * y_end_ratio))
            if y2 <= y1:
                continue
            center_x = self._band_center_x(edges[y1:y2, :])
            if center_x is not None:
                centers.append(center_x)

        if len(centers) >= 2:
            center_spread_ratio = float(np.std(centers)) / max(float(w), 1.0)
            alignment_score = _score_lower_better(
                center_spread_ratio,
                self.config.quality_band_alignment_good_ratio,
                self.config.quality_band_alignment_bad_ratio,
            )
        else:
            alignment_score = 0.5

        y1 = int(h * 0.25)
        y2 = int(h * 0.80)
        center_band = edges[y1:y2, :]
        if center_band.size == 0 or w < 2:
            return alignment_score, 0.5

        left_density = float(np.mean(center_band[:, : w // 2] > 0))
        right_density = float(np.mean(center_band[:, w // 2 :] > 0))
        lr_sum = left_density + right_density
        lr_imbalance = abs(left_density - right_density) / max(lr_sum, 1e-6)
        pose_score = _score_lower_better(
            lr_imbalance,
            self.config.quality_pose_balance_good,
            self.config.quality_pose_balance_bad,
        )
        return _clamp01(alignment_score), _clamp01(pose_score)

    def _compute_exposure_metrics(self, gray):
        gray_f = gray.astype(np.float32, copy=False)
        brightness = float(np.mean(gray_f))
        contrast = float(np.std(gray_f))

        dark_ratio = float(np.mean(gray_f <= self.config.quality_dark_intensity_threshold))
        bright_ratio = float(np.mean(gray_f >= self.config.quality_bright_intensity_threshold))

        p5, p95 = np.percentile(gray_f, [5, 95])
        dynamic_range = float(p95 - p5)

        underexposed_score = _score_lower_better(
            dark_ratio,
            self.config.quality_dark_ratio_good,
            self.config.quality_dark_ratio_bad,
        )
        overexposed_score = _score_lower_better(
            bright_ratio,
            self.config.quality_bright_ratio_good,
            self.config.quality_bright_ratio_bad,
        )
        range_score = _score_higher_better(
            dynamic_range,
            self.config.quality_dynamic_range_low,
            self.config.quality_dynamic_range_high,
        )

        exposure_score = _clamp01(0.45 * underexposed_score + 0.45 * overexposed_score + 0.10 * range_score)
        contrast_score = _three_level_score(
            contrast,
            self.config.quality_contrast_low,
            self.config.quality_contrast_high,
        )

        return {
            "brightness": brightness,
            "contrast": contrast,
            "dark_ratio": dark_ratio,
            "bright_ratio": bright_ratio,
            "dynamic_range": dynamic_range,
            "exposure_score": exposure_score,
            "contrast_score": contrast_score,
        }

    def _compute_detail_metrics(self, gray, edges):
        edge_density = float(np.mean(edges > 0))
        edge_density_score = _score_higher_better(
            edge_density,
            self.config.quality_edge_density_low,
            self.config.quality_edge_density_high,
        )

        gray_small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        blocks = gray_small.reshape(4, 8, 4, 8)
        local_std = blocks.std(axis=(1, 3))
        low_detail_ratio = float(np.mean(local_std < self.config.quality_low_detail_std_threshold))

        low_detail_score = _score_lower_better(
            low_detail_ratio,
            self.config.quality_low_detail_ratio_good,
            self.config.quality_low_detail_ratio_bad,
        )
        occlusion_score = _clamp01(0.6 * edge_density_score + 0.4 * low_detail_score)

        return {
            "edge_density": edge_density,
            "low_detail_ratio": low_detail_ratio,
            "occlusion_score": occlusion_score,
        }

    def assess_face_quality(self, face_crop, detection_confidence=None, landmarks=None):
        if face_crop is None or face_crop.size == 0:
            debug_info = {
                "sharpness": 0.0,
                "brightness": 0.0,
                "contrast": 0.0,
                "alignment_score": 0.0,
                "detection_confidence": float(detection_confidence) if detection_confidence is not None else 0.0,
            }
            return 0.0, "Poor", debug_info

        h, w = face_crop.shape[:2]
        area = h * w
        gray = _to_grayscale(face_crop)

        size_score = _three_level_score(area, self.config.quality_face_area_low, self.config.quality_face_area_high)

        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharpness_score = _three_level_score(
            laplacian_var,
            self.config.quality_sharpness_low,
            self.config.quality_sharpness_high,
        )

        confidence_value = float(detection_confidence) if detection_confidence is not None else 0.5
        confidence_value = _clamp01(confidence_value)
        detection_score = _three_level_score(
            confidence_value,
            self.config.quality_detection_confidence_low,
            self.config.quality_detection_confidence_high,
        )

        exposure_metrics = self._compute_exposure_metrics(gray)

        edges = cv2.Canny(gray, self.config.quality_canny_low, self.config.quality_canny_high)
        detail_metrics = self._compute_detail_metrics(gray, edges)

        normalized_landmarks = _normalize_landmarks(landmarks)
        if normalized_landmarks is not None:
            alignment_score, pose_score = self._alignment_pose_from_landmarks(normalized_landmarks)
            alignment_source = "landmarks"
        else:
            alignment_score, pose_score = self._approximate_alignment_pose(edges)
            alignment_source = "approx"

        weighted_sum = (
            self.config.quality_weight_size * size_score
            + self.config.quality_weight_sharpness * sharpness_score
            + self.config.quality_weight_detection_confidence * detection_score
            + self.config.quality_weight_alignment * alignment_score
            + self.config.quality_weight_pose * pose_score
            + self.config.quality_weight_exposure * exposure_metrics["exposure_score"]
            + self.config.quality_weight_contrast * exposure_metrics["contrast_score"]
            + self.config.quality_weight_occlusion * detail_metrics["occlusion_score"]
        )

        weights_total = (
            self.config.quality_weight_size
            + self.config.quality_weight_sharpness
            + self.config.quality_weight_detection_confidence
            + self.config.quality_weight_alignment
            + self.config.quality_weight_pose
            + self.config.quality_weight_exposure
            + self.config.quality_weight_contrast
            + self.config.quality_weight_occlusion
        )
        quality_score = _clamp01(weighted_sum / max(weights_total, 1e-6))

        if quality_score >= self.config.face_quality_good_threshold:
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
            "dark_ratio": exposure_metrics["dark_ratio"],
            "bright_ratio": exposure_metrics["bright_ratio"],
            "edge_density": detail_metrics["edge_density"],
            "low_detail_ratio": detail_metrics["low_detail_ratio"],
            "occlusion_score": detail_metrics["occlusion_score"],
            "alignment_source": alignment_source,
        }

        return quality_score, quality_status, debug_info
