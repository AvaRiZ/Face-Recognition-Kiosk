from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from core.config import AppConfig


def _clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _non_negative_finite(value, default=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)

    if not np.isfinite(parsed):
        return float(default)
    return max(0.0, parsed)


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
        self._quality_history_by_track = {}

    def reset_track_quality_history(self, track_id):
        if track_id is None:
            return
        try:
            key = int(track_id)
        except (TypeError, ValueError):
            return
        self._quality_history_by_track.pop(key, None)

    def reset_stale_track_quality_history(self, stale_track_ids):
        if not stale_track_ids:
            return
        for track_id in stale_track_ids:
            self.reset_track_quality_history(track_id)

    def _apply_temporal_smoothing(self, raw_quality_score, track_id):
        raw_score = _clamp01(raw_quality_score)
        if not self.config.quality_temporal_smoothing_enabled or track_id is None:
            return raw_score, 1, False

        try:
            key = int(track_id)
        except (TypeError, ValueError):
            return raw_score, 1, False

        window = max(int(self.config.quality_temporal_smoothing_window), 1)
        history = self._quality_history_by_track.get(key)

        if history is None or history.maxlen != window:
            old_values = list(history) if history is not None else []
            history = deque(old_values[-window:], maxlen=window)
            self._quality_history_by_track[key] = history

        history.append(raw_score)
        smoothed_score = float(np.mean(history))
        return _clamp01(smoothed_score), len(history), True

    def _compute_shared_quality_inputs(self, face_crop):
        h, w = face_crop.shape[:2]
        gray = _to_grayscale(face_crop)
        gray_f = gray.astype(np.float32, copy=False)
        edges = cv2.Canny(gray, self.config.quality_canny_low, self.config.quality_canny_high)
        sobel_magnitude = self._compute_sobel_magnitude(gray)
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        return {
            "height": h,
            "width": w,
            "area": h * w,
            "gray": gray,
            "gray_f": gray_f,
            "edges": edges,
            "sobel_magnitude": sobel_magnitude,
            "laplacian_var": laplacian_var,
        }

    def _compute_size_score(self, area):
        hard_min = max(int(self.config.quality_face_area_hard_min), 1)
        low = max(int(self.config.quality_face_area_low), hard_min + 1)
        high = max(int(self.config.quality_face_area_high), low + 1)
        exponent = max(float(self.config.quality_size_curve_exponent), 1.0)

        if area <= hard_min:
            return 0.0

        if area < low:
            ratio = (float(area) - hard_min) / max(float(low - hard_min), 1.0)
            return _clamp01(0.35 * (ratio**exponent))

        if area < high:
            ratio = (float(area) - low) / max(float(high - low), 1.0)
            return _clamp01(0.35 + 0.65 * ratio)

        return 1.0

    def _compute_sobel_magnitude(self, gray):
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        return cv2.magnitude(sobel_x, sobel_y)

    def _normalized_weighted_score(self, component_scores, component_weights):
        weighted_sum = 0.0
        weights_total = 0.0
        weighted_components = {}

        for name, score in component_scores.items():
            if score is None:
                continue

            score_value = float(score)
            if not np.isfinite(score_value):
                continue

            weight = float(component_weights.get(name, 0.0))
            if weight <= 0.0:
                continue

            score_value = _clamp01(score_value)
            weighted = weight * score_value
            weighted_sum += weighted
            weights_total += weight
            weighted_components[name] = weighted

        if weights_total <= 1e-6:
            return 0.0, weighted_sum, weights_total, weighted_components

        return _clamp01(weighted_sum / weights_total), weighted_sum, weights_total, weighted_components

    def _component_weights(self):
        return {
            "size": _non_negative_finite(self.config.quality_weight_size),
            "sharpness": _non_negative_finite(self.config.quality_weight_sharpness),
            "detection": _non_negative_finite(self.config.quality_weight_detection_confidence),
            "alignment": _non_negative_finite(self.config.quality_weight_alignment),
            "pose": _non_negative_finite(self.config.quality_weight_pose),
            "exposure": _non_negative_finite(self.config.quality_weight_exposure),
            "contrast": _non_negative_finite(self.config.quality_weight_contrast),
            "occlusion": _non_negative_finite(self.config.quality_weight_occlusion),
        }

    def _confidence_adjusted_component_weights(self, base_weights, detection_score, alignment_source):
        effective_weights = dict(base_weights)
        if not self.config.quality_confidence_weighting_enabled:
            return effective_weights, 1.0, 1.0, False

        confidence_value = _clamp01(detection_score)
        confidence_floor = _clamp01(self.config.quality_confidence_weight_floor)
        align_pose_multiplier = confidence_floor + (1.0 - confidence_floor) * confidence_value

        if alignment_source != "landmarks":
            align_pose_multiplier *= max(float(self.config.quality_confidence_weight_approx_factor), 0.0)

        detection_multiplier = 1.0 + max(float(self.config.quality_confidence_detection_weight_boost), 0.0) * (
            1.0 - confidence_value
        )

        effective_weights["alignment"] = max(0.0, effective_weights["alignment"] * align_pose_multiplier)
        effective_weights["pose"] = max(0.0, effective_weights["pose"] * align_pose_multiplier)
        effective_weights["detection"] = max(0.0, effective_weights["detection"] * detection_multiplier)

        return effective_weights, align_pose_multiplier, detection_multiplier, True

    def _hard_gate_reason(self, shared_inputs, exposure_metrics):
        if not self.config.quality_hard_gate_enabled:
            return None

        area = float(shared_inputs["area"])
        laplacian_var = float(shared_inputs["laplacian_var"])
        dynamic_range = float(exposure_metrics["dynamic_range"])
        shadow_clip_ratio = float(exposure_metrics["shadow_clip_ratio"])
        highlight_clip_ratio = float(exposure_metrics["highlight_clip_ratio"])

        if area < float(self.config.quality_hard_gate_min_face_area):
            return "hard_gate: face area too small"
        if laplacian_var < float(self.config.quality_hard_gate_min_laplacian):
            return "hard_gate: laplacian too low"
        if dynamic_range < float(self.config.quality_hard_gate_min_dynamic_range):
            return "hard_gate: dynamic range too low"
        if shadow_clip_ratio > float(self.config.quality_hard_gate_max_shadow_clip_ratio):
            return "hard_gate: excessive shadow clipping"
        if highlight_clip_ratio > float(self.config.quality_hard_gate_max_highlight_clip_ratio):
            return "hard_gate: excessive highlight clipping"

        return None

    def _apply_soft_gate(self, raw_quality_score):
        raw_score = _clamp01(raw_quality_score)
        if not self.config.quality_soft_gate_enabled:
            return raw_score, 1.0, False

        floor = _clamp01(self.config.quality_soft_gate_floor)
        min_multiplier = _clamp01(self.config.quality_soft_gate_min_multiplier)

        if raw_score >= floor or floor <= 1e-6:
            return raw_score, 1.0, False

        ratio = raw_score / floor
        soft_multiplier = min_multiplier + (1.0 - min_multiplier) * ratio
        adjusted = _clamp01(raw_score * soft_multiplier)
        return adjusted, soft_multiplier, True

    @staticmethod
    def _component_health_label(score):
        if score >= 0.80:
            return "strong"
        if score >= 0.60:
            return "ok"
        if score >= 0.40:
            return "weak"
        return "critical"

    def _build_explainability_debug(
        self,
        component_scores,
        base_weights,
        effective_weights,
        weighted_components,
        weighted_sum,
        raw_quality_score,
        soft_gated_score,
        quality_score,
        hard_gate_triggered,
        hard_gate_reason,
        soft_gate_applied,
        soft_gate_multiplier,
        smoothing_applied,
        smoothing_samples,
        confidence_weighting_applied,
        align_pose_multiplier,
        detection_multiplier,
        alignment_source,
    ):
        if not self.config.quality_explainability_enabled:
            return {}

        top_k = max(int(self.config.quality_explainability_top_k), 1)
        factor_entries = []
        for name, score in component_scores.items():
            score_value = _clamp01(score)
            base_weight = float(base_weights.get(name, 0.0))
            effective_weight = float(effective_weights.get(name, 0.0))
            weighted = float(weighted_components.get(name, 0.0))
            contribution_ratio = weighted / max(float(weighted_sum), 1e-6)
            factor_entries.append(
                {
                    "name": name,
                    "score": score_value,
                    "label": self._component_health_label(score_value),
                    "base_weight": base_weight,
                    "effective_weight": effective_weight,
                    "weighted": weighted,
                    "contribution_ratio": float(_clamp01(contribution_ratio)),
                }
            )

        weakest = sorted(factor_entries, key=lambda item: item["score"])[:top_k]
        strongest = sorted(factor_entries, key=lambda item: item["weighted"], reverse=True)[:top_k]

        if hard_gate_triggered:
            summary = hard_gate_reason or "hard gate triggered"
        else:
            weakest_text = ", ".join(
                f"{item['name']}={item['score']:.2f}" for item in weakest
            )
            summary = f"weakest factors: {weakest_text}" if weakest_text else "no factor diagnostics"
            if soft_gate_applied:
                summary += f"; soft gate x{soft_gate_multiplier:.2f}"
            if smoothing_applied:
                summary += f"; smoothed over {smoothing_samples} samples"

        return {
            "summary": summary,
            "weakest_factors": weakest,
            "strongest_contributors": strongest,
            "decision_path": {
                "raw_quality_score": float(raw_quality_score),
                "soft_gated_score": float(soft_gated_score),
                "final_quality_score": float(quality_score),
                "hard_gate_triggered": bool(hard_gate_triggered),
                "hard_gate_reason": str(hard_gate_reason or ""),
                "soft_gate_applied": bool(soft_gate_applied),
                "soft_gate_multiplier": float(soft_gate_multiplier),
                "smoothing_applied": bool(smoothing_applied),
                "smoothing_samples": int(smoothing_samples),
            },
            "weighting_adjustments": {
                "confidence_weighting_applied": bool(confidence_weighting_applied),
                "align_pose_weight_multiplier": float(align_pose_multiplier),
                "detection_weight_multiplier": float(detection_multiplier),
                "alignment_source": str(alignment_source),
            },
        }

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

    def _compute_exposure_metrics(self, gray_f):
        brightness = float(np.mean(gray_f))
        contrast = float(np.std(gray_f))

        dark_ratio = float(np.mean(gray_f <= self.config.quality_dark_intensity_threshold))
        bright_ratio = float(np.mean(gray_f >= self.config.quality_bright_intensity_threshold))

        p5, p95 = np.percentile(gray_f, [5, 95])
        dynamic_range = float(p95 - p5)
        shadow_clip_ratio = float(np.mean(gray_f <= self.config.quality_shadow_clip_intensity_threshold))
        highlight_clip_ratio = float(np.mean(gray_f >= self.config.quality_highlight_clip_intensity_threshold))

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
        shadow_clip_score = _score_lower_better(
            shadow_clip_ratio,
            self.config.quality_shadow_clip_ratio_good,
            self.config.quality_shadow_clip_ratio_bad,
        )
        highlight_clip_score = _score_lower_better(
            highlight_clip_ratio,
            self.config.quality_highlight_clip_ratio_good,
            self.config.quality_highlight_clip_ratio_bad,
        )

        dynamic_range_component = _clamp01(0.50 * range_score + 0.25 * shadow_clip_score + 0.25 * highlight_clip_score)

        exposure_score = _clamp01(0.35 * underexposed_score + 0.35 * overexposed_score + 0.30 * dynamic_range_component)
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
            "shadow_clip_ratio": shadow_clip_ratio,
            "highlight_clip_ratio": highlight_clip_ratio,
            "dynamic_range_component": dynamic_range_component,
            "exposure_score": exposure_score,
            "contrast_score": contrast_score,
        }

    def _compute_detail_metrics(self, gray, sobel_magnitude):
        edge_density = float(np.mean(sobel_magnitude >= self.config.quality_edge_magnitude_threshold))
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

    def assess_face_quality(self, face_crop, detection_confidence=None, landmarks=None, track_id=None):
        if face_crop is None or face_crop.size == 0:
            debug_info = {
                "sharpness": 0.0,
                "brightness": 0.0,
                "contrast": 0.0,
                "alignment_score": 0.0,
                "detection_confidence": float(detection_confidence) if detection_confidence is not None else 0.0,
                "raw_quality_score": 0.0,
                "smoothed_quality_score": 0.0,
                "smoothing_applied": False,
                "smoothing_samples": 0,
                "track_id": track_id,
                "hard_gate_triggered": True,
                "hard_gate_reason": "hard_gate: empty face crop",
                "soft_gate_applied": False,
                "soft_gate_multiplier": 1.0,
            }
            return 0.0, "Poor", debug_info

        shared_inputs = self._compute_shared_quality_inputs(face_crop)
        area = shared_inputs["area"]

        size_score = self._compute_size_score(area)

        laplacian_var = shared_inputs["laplacian_var"]
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

        exposure_metrics = self._compute_exposure_metrics(shared_inputs["gray_f"])
        detail_metrics = self._compute_detail_metrics(shared_inputs["gray"], shared_inputs["sobel_magnitude"])

        normalized_landmarks = _normalize_landmarks(landmarks)
        if normalized_landmarks is not None:
            alignment_score, pose_score = self._alignment_pose_from_landmarks(normalized_landmarks)
            alignment_source = "landmarks"
        else:
            alignment_score, pose_score = self._approximate_alignment_pose(shared_inputs["edges"])
            alignment_source = "approx"

        component_scores = {
            "size": size_score,
            "sharpness": sharpness_score,
            "detection": detection_score,
            "alignment": alignment_score,
            "pose": pose_score,
            "exposure": exposure_metrics["exposure_score"],
            "contrast": exposure_metrics["contrast_score"],
            "occlusion": detail_metrics["occlusion_score"],
        }
        component_weights = self._component_weights()
        effective_component_weights, align_pose_multiplier, detection_multiplier, confidence_weighting_applied = (
            self._confidence_adjusted_component_weights(
                component_weights,
                detection_score,
                alignment_source,
            )
        )
        raw_quality_score, weighted_sum, weights_total, weighted_components = self._normalized_weighted_score(
            component_scores,
            effective_component_weights,
        )

        hard_gate_reason = self._hard_gate_reason(shared_inputs, exposure_metrics)
        hard_gate_triggered = hard_gate_reason is not None
        soft_gated_score = raw_quality_score
        soft_gate_multiplier = 1.0
        soft_gate_applied = False

        if hard_gate_triggered:
            quality_score = 0.0
            smoothing_samples = 0
            smoothing_applied = False
        else:
            soft_gated_score, soft_gate_multiplier, soft_gate_applied = self._apply_soft_gate(raw_quality_score)
            quality_score, smoothing_samples, smoothing_applied = self._apply_temporal_smoothing(
                soft_gated_score,
                track_id,
            )

        explainability_debug = self._build_explainability_debug(
            component_scores=component_scores,
            base_weights=component_weights,
            effective_weights=effective_component_weights,
            weighted_components=weighted_components,
            weighted_sum=weighted_sum,
            raw_quality_score=raw_quality_score,
            soft_gated_score=soft_gated_score,
            quality_score=quality_score,
            hard_gate_triggered=hard_gate_triggered,
            hard_gate_reason=hard_gate_reason,
            soft_gate_applied=soft_gate_applied,
            soft_gate_multiplier=soft_gate_multiplier,
            smoothing_applied=smoothing_applied,
            smoothing_samples=smoothing_samples,
            confidence_weighting_applied=confidence_weighting_applied,
            align_pose_multiplier=align_pose_multiplier,
            detection_multiplier=detection_multiplier,
            alignment_source=alignment_source,
        )

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
            "face_area": area,
            "size_score": size_score,
            "sharpness_score": sharpness_score,
            "exposure_score": exposure_metrics["exposure_score"],
            "dark_ratio": exposure_metrics["dark_ratio"],
            "bright_ratio": exposure_metrics["bright_ratio"],
            "dynamic_range": exposure_metrics["dynamic_range"],
            "shadow_clip_ratio": exposure_metrics["shadow_clip_ratio"],
            "highlight_clip_ratio": exposure_metrics["highlight_clip_ratio"],
            "dynamic_range_component": exposure_metrics["dynamic_range_component"],
            "edge_density": detail_metrics["edge_density"],
            "low_detail_ratio": detail_metrics["low_detail_ratio"],
            "occlusion_score": detail_metrics["occlusion_score"],
            "component_weights": component_weights,
            "effective_component_weights": effective_component_weights,
            "confidence_weighting_applied": confidence_weighting_applied,
            "align_pose_weight_multiplier": align_pose_multiplier,
            "detection_weight_multiplier": detection_multiplier,
            "weighted_sum": weighted_sum,
            "weights_total": weights_total,
            "weighted_components": weighted_components,
            "raw_quality_score": raw_quality_score,
            "soft_gated_score": soft_gated_score,
            "smoothed_quality_score": quality_score,
            "smoothing_applied": smoothing_applied,
            "smoothing_samples": smoothing_samples,
            "hard_gate_triggered": hard_gate_triggered,
            "hard_gate_reason": hard_gate_reason or "",
            "soft_gate_applied": soft_gate_applied,
            "soft_gate_multiplier": soft_gate_multiplier,
            "track_id": track_id,
            "alignment_source": alignment_source,
            "explainability": explainability_debug,
            "explainability_summary": explainability_debug.get("summary", ""),
        }

        return quality_score, quality_status, debug_info
