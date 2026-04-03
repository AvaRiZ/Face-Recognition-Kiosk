from __future__ import annotations

import os
import statistics
import time
from collections import deque

import cv2
import numpy as np

from core.config import AppConfig
from core.models import RecognitionResult, RegistrationSample, recognized_user_payload
from core.state import AppStateManager
from database.repository import UserRepository
from services.dataset_service import DetectorDatasetService
from services.embedding_service import EmbeddingService, count_embeddings, first_embedding


class FaceRecognitionService:
    def __init__(
        self,
        config: AppConfig,
        state: AppStateManager,
        repository: UserRepository,
        embedding_service: EmbeddingService,
    ):
        self.config = config
        self.state = state
        self.repository = repository
        self.embedding_service = embedding_service
        self.detector_dataset_service = DetectorDatasetService(config)
        self.recognition_history: dict[int, deque[float]] = {}
        self.confidence_smoothing: dict[int, deque[float]] = {}

    @staticmethod
    def _min_cosine_distance(query_embedding: np.ndarray, existing_embeddings: list[np.ndarray]) -> float | None:
        if not isinstance(query_embedding, np.ndarray) or query_embedding.ndim != 1 or query_embedding.size == 0:
            return None

        candidates = [
            emb
            for emb in existing_embeddings
            if isinstance(emb, np.ndarray) and emb.ndim == 1 and emb.shape == query_embedding.shape and emb.size > 0
        ]
        if not candidates:
            return None

        stacked = np.vstack(candidates)
        similarities = stacked @ query_embedding
        distances = 1.0 - similarities
        return float(np.min(distances))

    def _try_add_learning_embedding(
        self,
        user,
        model_name: str,
        new_embedding: np.ndarray | None,
        quality_score: float,
    ) -> bool:
        if new_embedding is None or not isinstance(new_embedding, np.ndarray):
            return False
        if new_embedding.ndim != 1 or new_embedding.size == 0:
            return False
        if quality_score < self.config.recognition_min_quality_for_learning:
            return False

        model_embeddings = user.embeddings.setdefault(model_name, [])
        min_distance = self._min_cosine_distance(new_embedding, model_embeddings)
        if (
            min_distance is not None
            and min_distance < self.config.embedding_novelty_min_distance
        ):
            return False

        model_embeddings.append(new_embedding.astype(np.float32, copy=False))

        max_keep = max(int(self.config.max_embeddings_per_user_per_model), 1)
        if len(model_embeddings) > max_keep:
            del model_embeddings[:-max_keep]
        return True

    def calculate_dynamic_threshold(self, user_id: int, face_quality: float) -> float:
        base_threshold = self.state.base_threshold

        if face_quality < 0.5:
            quality_adjustment = 0.1
        elif face_quality < 0.7:
            quality_adjustment = 0.05
        else:
            quality_adjustment = -0.05

        if user_id in self.recognition_history and len(self.recognition_history[user_id]) > 0:
            avg_confidence = statistics.mean(self.recognition_history[user_id])
            history_adjustment = (0.5 - avg_confidence) * 0.2
        else:
            history_adjustment = 0.0

        dynamic_threshold = base_threshold + quality_adjustment + history_adjustment
        return max(0.2, min(0.6, dynamic_threshold))

    def smooth_confidence(self, user_id: int, confidence: float) -> float:
        if user_id not in self.confidence_smoothing:
            self.confidence_smoothing[user_id] = deque(maxlen=self.config.confidence_smoothing_window)
        self.confidence_smoothing[user_id].append(confidence)
        return statistics.mean(self.confidence_smoothing[user_id])

    def find_best_match(self, query_embeddings, face_quality: float) -> RecognitionResult | None:
        if self.config.primary_model not in query_embeddings or self.config.secondary_model not in query_embeddings:
            print("  [WARN] Need embeddings from both models for two-factor verification")
            return None

        primary_emb = first_embedding(query_embeddings, self.config.primary_model)
        secondary_emb = first_embedding(query_embeddings, self.config.secondary_model)
        if primary_emb is None or secondary_emb is None:
            return None

        best_match: RecognitionResult | None = None

        users = self.state.users
        for user_idx, user in enumerate(users):
            user_embeddings_by_model = user.embeddings
            if self.config.primary_model not in user_embeddings_by_model:
                continue
            if self.config.secondary_model not in user_embeddings_by_model:
                continue

            primary_best_dist = float("inf")
            for user_embedding in user_embeddings_by_model.get(self.config.primary_model, []):
                if not isinstance(user_embedding, np.ndarray):
                    continue
                if user_embedding.size == 0 or user_embedding.ndim != 1:
                    continue
                if primary_emb.shape != user_embedding.shape:
                    continue
                distance = 1 - float(np.dot(primary_emb, user_embedding))
                primary_best_dist = min(primary_best_dist, distance)

            secondary_best_dist = float("inf")
            for user_embedding in user_embeddings_by_model.get(self.config.secondary_model, []):
                if not isinstance(user_embedding, np.ndarray):
                    continue
                if user_embedding.size == 0 or user_embedding.ndim != 1:
                    continue
                if secondary_emb.shape != user_embedding.shape:
                    continue
                distance = 1 - float(np.dot(secondary_emb, user_embedding))
                secondary_best_dist = min(secondary_best_dist, distance)

            if primary_best_dist == float("inf") or secondary_best_dist == float("inf"):
                continue

            primary_confidence = 1 - primary_best_dist
            secondary_confidence = 1 - secondary_best_dist

            dynamic_threshold = self.calculate_dynamic_threshold(user.id, face_quality)
            primary_threshold = max(self.config.primary_threshold, dynamic_threshold)
            secondary_threshold = max(self.config.secondary_threshold, dynamic_threshold)

            primary_pass = primary_confidence >= primary_threshold
            secondary_pass = secondary_confidence >= secondary_threshold

            if not (primary_pass and secondary_pass):
                continue

            avg_confidence = (primary_confidence + secondary_confidence) / 2
            avg_distance = (primary_best_dist + secondary_best_dist) / 2

            if best_match is None or avg_confidence > best_match.confidence:
                best_match = RecognitionResult(
                    user_id=user.id,
                    confidence=avg_confidence,
                    primary_confidence=primary_confidence,
                    secondary_confidence=secondary_confidence,
                    distance=avg_distance,
                    primary_distance=primary_best_dist,
                    secondary_distance=secondary_best_dist,
                    threshold=(primary_threshold + secondary_threshold) / 2,
                    user=user,
                    user_index=user_idx,
                )

        if best_match:
            if best_match.user_id not in self.recognition_history:
                self.recognition_history[best_match.user_id] = deque(maxlen=50)
            self.recognition_history[best_match.user_id].append(best_match.confidence)
            self.repository.log_recognition(best_match, face_quality=face_quality, method="two-factor")
            print(
                f"  [OK] 2-Factor Verified: {best_match.user.name} "
                f"(ArcFace={best_match.primary_confidence:.2%}, Facenet={best_match.secondary_confidence:.2%})"
            )

        return best_match

    def recognize(self, face_crop, quality_score: float):
        embeddings = self.embedding_service.extract_embedding_ensemble(face_crop)
        if not embeddings:
            return None, {}
        return self.find_best_match(embeddings, quality_score), embeddings

    def register_or_recognize_face(
        self,
        face_crop,
        quality_service,
        face_id=None,
        allow_registration: bool = False,
        detection_confidence=None,
        landmarks=None,
        precomputed_quality=None,
    ):
        reg_state = self.state.registration_state
        if reg_state.in_progress:
            return None

        if precomputed_quality is None:
            quality_score, quality_status, quality_debug = quality_service.assess_face_quality(
                face_crop,
                detection_confidence=detection_confidence,
                landmarks=landmarks,
            )
        else:
            quality_score, quality_status, quality_debug = precomputed_quality

        if quality_score < self.state.face_quality_threshold:
            print(f"  Skipping low quality face: {quality_score:.2f} ({quality_status})")
            return None

        print(
            f"  Face quality: {quality_score:.2f} ({quality_status}) "
            f"| sharpness={quality_debug.get('sharpness', 0.0):.1f} "
            f"| det={quality_debug.get('detection_confidence', 0.0):.2f}"
        )

        best_match, embeddings = self.recognize(face_crop, quality_score)
        if not embeddings:
            print("  Failed to extract embeddings")
            return None

        if best_match:
            primary_new = first_embedding(embeddings, self.config.primary_model)
            secondary_new = first_embedding(embeddings, self.config.secondary_model)

            learned_by_model: dict[str, list[np.ndarray]] = {}
            if self._try_add_learning_embedding(
                best_match.user,
                self.config.primary_model,
                primary_new,
                quality_score,
            ):
                learned_by_model[self.config.primary_model] = [primary_new]

            if self._try_add_learning_embedding(
                best_match.user,
                self.config.secondary_model,
                secondary_new,
                quality_score,
            ):
                learned_by_model[self.config.secondary_model] = [secondary_new]

            active_user = best_match.user
            if learned_by_model:
                timestamp = int(time.time() * 1000)
                user_folder = os.path.join(self.config.base_save_dir, best_match.user.sr_code)
                os.makedirs(user_folder, exist_ok=True)
                filename = os.path.join(user_folder, f"face_{timestamp}_learned.jpg")
                cv2.imwrite(filename, face_crop)

                dataset_entry = self.detector_dataset_service.save_recognized_face_crop(
                    face_crop=face_crop,
                    sr_code=best_match.user.sr_code,
                    timestamp=timestamp,
                )
                if dataset_entry:
                    dataset_image_path, _dataset_label_path = dataset_entry
                    print(f"[OK] Added recognized crop to detector training dataset: {dataset_image_path}")

                updated_user = self.repository.overwrite_embeddings(
                    best_match.user_id,
                    best_match.user.embeddings,
                    image_path=filename,
                )
                if updated_user:
                    self.state.replace_user(updated_user)
                    active_user = updated_user
                else:
                    self.state.replace_user(best_match.user)

                total_embeddings = count_embeddings(active_user.embeddings)
                print(
                    f"[OK] Learned embedding update for {active_user.name} "
                    f"(total: {total_embeddings} embeddings across models)"
                )
            else:
                self.state.replace_user(best_match.user)
                print("[INFO] Recognition accepted, but learning skipped (quality/novelty gate).")

            recognized_payload = recognized_user_payload(best_match)
            self.state.set_recognized_user(recognized_payload)
            reg_state.in_progress = False
            print(
                f"[OK] Recognized: {recognized_payload['name']} "
                f"(conf: {best_match.confidence:.2%}, dist: {best_match.distance:.4f})"
            )
            return True

        if allow_registration and not reg_state.in_progress and reg_state.capture_count < reg_state.max_captures:
            sample = RegistrationSample(face_crop=face_crop, embeddings=embeddings, quality=quality_score)
            captured_count = self.state.capture_registration_sample(sample)
            print(f"  Captured face {captured_count}/{reg_state.max_captures} for registration")

            if reg_state.in_progress and reg_state.pending_registration:
                print(
                    f"[WARN] New face detected - Ready for registration with "
                    f"{len(reg_state.pending_registration)} samples"
                )

        return False
