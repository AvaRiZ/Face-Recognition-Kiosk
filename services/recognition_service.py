from __future__ import annotations

import os
import statistics
import time
from collections import deque
from typing import Any

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
        self._vector_indexes: dict[str, dict[str, Any]] = {}
        self._index_signature: tuple[tuple[int, int, int], ...] | None = None

    def _compute_index_signature(self) -> tuple[tuple[int, int, int], ...]:
        signature: list[tuple[int, int, int]] = []
        for user in self.state.users:
            primary_count = len(user.embeddings.get(self.config.primary_model, []))
            secondary_count = len(user.embeddings.get(self.config.secondary_model, []))
            signature.append((int(user.id), int(primary_count), int(secondary_count)))
        signature.sort(key=lambda item: item[0])
        return tuple(signature)

    def _normalize_vector(self, vector: np.ndarray) -> np.ndarray | None:
        if not isinstance(vector, np.ndarray):
            return None
        if vector.ndim != 1 or vector.size == 0:
            return None
        vec = vector.astype(np.float32, copy=False)
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return None
        return vec / norm

    def _build_index_for_model(self, model_name: str) -> dict[str, Any]:
        vectors: list[np.ndarray] = []
        user_ids: list[int] = []
        dim: int | None = None

        for user in self.state.users:
            for embedding in user.embeddings.get(model_name, []):
                emb = self._normalize_vector(embedding)
                if emb is None:
                    continue
                if dim is None:
                    dim = int(emb.shape[0])
                if emb.shape[0] != dim:
                    continue
                vectors.append(emb)
                user_ids.append(int(user.id))

        if not vectors or dim is None:
            return {"vectors": None, "user_ids": [], "dim": None}

        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        return {"vectors": matrix, "user_ids": user_ids, "dim": dim}

    def _ensure_vector_indexes(self) -> None:
        signature = self._compute_index_signature()
        if signature == self._index_signature:
            return

        self._vector_indexes = {
            self.config.primary_model: self._build_index_for_model(self.config.primary_model),
            self.config.secondary_model: self._build_index_for_model(self.config.secondary_model),
        }
        self._index_signature = signature

    def _query_candidate_user_ids(self, model_name: str, query_emb: np.ndarray) -> set[int]:
        index = self._vector_indexes.get(model_name, {})
        matrix = index.get("vectors")
        user_ids = index.get("user_ids") or []
        dim = index.get("dim")
        if matrix is None or dim is None or not user_ids:
            return set()
        if query_emb.shape[0] != dim:
            return set()

        configured_top_k = max(1, int(self.config.vector_index_top_k))
        neighbor_count = min(configured_top_k, len(user_ids))
        if neighbor_count <= 0:
            return set()

        scores = matrix @ query_emb
        if scores.size == 0:
            return set()

        if neighbor_count >= scores.shape[0]:
            indices = np.argsort(-scores)
        else:
            top_idx = np.argpartition(-scores, neighbor_count - 1)[:neighbor_count]
            indices = top_idx[np.argsort(-scores[top_idx])]

        return {int(user_ids[int(idx)]) for idx in indices if 0 <= int(idx) < len(user_ids)}

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

        primary_emb = self._normalize_vector(primary_emb)
        secondary_emb = self._normalize_vector(secondary_emb)
        if primary_emb is None or secondary_emb is None:
            return None

        self._ensure_vector_indexes()

        primary_candidates = self._query_candidate_user_ids(self.config.primary_model, primary_emb)
        secondary_candidates = self._query_candidate_user_ids(self.config.secondary_model, secondary_emb)
        candidate_user_ids = primary_candidates | secondary_candidates

        best_match: RecognitionResult | None = None

        users = self.state.users
        for user_idx, user in enumerate(users):
            if candidate_user_ids and user.id not in candidate_user_ids:
                continue

            user_embeddings_by_model = user.embeddings
            if self.config.primary_model not in user_embeddings_by_model:
                continue
            if self.config.secondary_model not in user_embeddings_by_model:
                continue

            primary_best_dist = float("inf")
            for user_embedding in user_embeddings_by_model.get(self.config.primary_model, []):
                normalized_user_embedding = self._normalize_vector(user_embedding)
                if normalized_user_embedding is None:
                    continue
                if primary_emb.shape != normalized_user_embedding.shape:
                    continue
                distance = 1 - float(np.dot(primary_emb, normalized_user_embedding))
                primary_best_dist = min(primary_best_dist, distance)

            secondary_best_dist = float("inf")
            for user_embedding in user_embeddings_by_model.get(self.config.secondary_model, []):
                normalized_user_embedding = self._normalize_vector(user_embedding)
                if normalized_user_embedding is None:
                    continue
                if secondary_emb.shape != normalized_user_embedding.shape:
                    continue
                distance = 1 - float(np.dot(secondary_emb, normalized_user_embedding))
                secondary_best_dist = min(secondary_best_dist, distance)

            if primary_best_dist == float("inf") or secondary_best_dist == float("inf"):
                continue

            primary_confidence = 1 - primary_best_dist
            secondary_confidence = 1 - secondary_best_dist

            primary_threshold = max(self.config.primary_threshold, self.state.base_threshold)
            secondary_threshold = max(self.config.secondary_threshold, self.state.base_threshold)

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

    def is_confident_recognition(self, match: RecognitionResult) -> bool:
        return float(match.confidence) >= float(self.config.recognition_confidence_threshold)

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
            return {"status": "registration_pending"}

        if precomputed_quality is None:
            quality_score, quality_status, quality_debug = quality_service.assess_face_quality(
                face_crop,
                detection_confidence=detection_confidence,
                landmarks=landmarks,
            )
        else:
            quality_score, quality_status, quality_debug = precomputed_quality

        if quality_score < self.state.face_quality_threshold:
            message = f"  Skipping low quality face: {quality_score:.2f} ({quality_status})"
            if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
                primary_issue = quality_debug.get("primary_issue_label")
                if primary_issue:
                    message += f" | main issue: {primary_issue}"
            if self.config.quality_debug_enabled and self.config.quality_debug_show_all_scores:
                message += f" | {quality_service.quality_debug_summary(quality_debug)}"
            print(message)
            return {
                "status": "low_quality",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": None,
                "match_threshold": None,
            }

        message = (
            f"  Face quality: {quality_score:.2f} ({quality_status}) "
            f"| sharpness={quality_debug.get('sharpness', 0.0):.1f} "
            f"| det={quality_debug.get('detection_confidence', 0.0):.2f}"
        )
        if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
            primary_issue = quality_debug.get("primary_issue_label")
            if primary_issue and quality_status != "Good":
                message += f" | weakest: {primary_issue}"
        if self.config.quality_debug_enabled and self.config.quality_debug_show_all_scores:
            message += f" | {quality_service.quality_debug_summary(quality_debug)}"
        print(message)

        best_match, embeddings = self.recognize(face_crop, quality_score)
        if not embeddings:
            print("  Failed to extract embeddings")
            return {
                "status": "embedding_failed",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": None,
                "match_threshold": None,
            }

        if best_match:
            if not self.is_confident_recognition(best_match):
                print(
                    f"  Recognition below confidence threshold: "
                    f"{best_match.confidence:.2%} < {self.config.recognition_confidence_threshold:.2%}"
                )
                return {
                    "status": "uncertain",
                    "quality_score": quality_score,
                    "quality_status": quality_status,
                    "quality_debug": quality_debug,
                    "match_confidence": best_match.confidence,
                    "match_threshold": max(best_match.threshold, self.config.recognition_confidence_threshold),
                }

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

            primary_new = first_embedding(embeddings, self.config.primary_model)
            secondary_new = first_embedding(embeddings, self.config.secondary_model)
            if primary_new is not None:
                best_match.user.embeddings.setdefault(self.config.primary_model, []).append(primary_new)
            if secondary_new is not None:
                best_match.user.embeddings.setdefault(self.config.secondary_model, []).append(secondary_new)

            updated_user = self.repository.update_embeddings(best_match.user_id, embeddings, image_path=filename)
            if updated_user:
                self.state.replace_user(updated_user)
                active_user = updated_user
            else:
                self.state.replace_user(best_match.user)
                active_user = best_match.user

            # Force index rebuild after online learning updates the embedding bank.
            self._index_signature = None

            total_embeddings = count_embeddings(active_user.embeddings)
            print(
                f"[OK] Learned new embedding for {active_user.name} "
                f"(total: {total_embeddings} embeddings across models)"
            )

            recognized_payload = recognized_user_payload(best_match)
            self.state.set_recognized_user(recognized_payload)
            reg_state.in_progress = False
            print(
                f"[OK] Recognized: {recognized_payload['name']} "
                f"(conf: {best_match.confidence:.2%}, dist: {best_match.distance:.4f})"
            )
            return {
                "status": "recognized",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": best_match.confidence,
                "match_threshold": max(best_match.threshold, self.config.recognition_confidence_threshold),
                "payload": recognized_payload,
            }

        if allow_registration and not reg_state.in_progress and reg_state.capture_count < reg_state.max_captures:
            current_pose = self.state.get_current_registration_pose() or "front"
            detected_pose = quality_service.detect_face_pose(face_crop, landmarks=landmarks)
            if detected_pose != current_pose:
                pose_label = detected_pose or "unknown"
                print(
                    f"  Skipped registration sample: expected pose '{current_pose}', detected '{pose_label}'"
                )
                return {
                    "status": "pose_mismatch",
                    "quality_score": quality_score,
                    "quality_status": quality_status,
                    "quality_debug": quality_debug,
                    "match_confidence": None,
                    "match_threshold": None,
                    "expected_pose": current_pose,
                    "detected_pose": detected_pose,
                }

            sample = RegistrationSample(
                face_crop=face_crop,
                embeddings=embeddings,
                quality=quality_score,
                pose=current_pose,
            )
            captured_count = self.state.capture_registration_sample(sample)
            print(
                f"  Captured registration sample for pose '{current_pose}' "
                f"({captured_count}/{reg_state.max_captures} valid captures)"
            )

            if reg_state.in_progress and reg_state.pending_registration:
                print(
                    f"[WARN] New face detected - Ready for registration with "
                    f"{len(reg_state.pending_registration)} samples"
                )

            return {
                "status": "registration_captured",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": None,
                "match_threshold": None,
                "expected_pose": current_pose,
                "detected_pose": detected_pose,
            }

        return {
            "status": "no_match",
            "quality_score": quality_score,
            "quality_status": quality_status,
            "quality_debug": quality_debug,
            "match_confidence": None,
            "match_threshold": None,
        }
