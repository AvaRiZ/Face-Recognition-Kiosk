from __future__ import annotations

import time
from typing import Any

import numpy as np

from core.config import AppConfig
from core.models import RecognitionResult, RegistrationSample, recognized_user_payload
from core.state import AppStateManager
from database.repository import UserRepository
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
        self._vector_indexes: dict[str, dict[str, Any]] = {}
        self._index_signature: tuple[tuple[int, int, int], ...] | None = None
        self._recognition_event_locks: dict[tuple[int, str], float] = {}
        self._last_match_diagnostics: dict[str, Any] = {}

    def _event_type_for_current_repository(self) -> str:
        camera_id = int(getattr(self.repository, "camera_id", 1) or 1)
        return "exit" if camera_id == 2 else "entry"

    def _presence_gate_allows_event(self, user_id: int, event_type: str) -> tuple[bool, str]:
        checker = getattr(self.repository, "check_presence_gate", None)
        if not callable(checker):
            return True, "gate_unavailable"
        try:
            payload = checker(int(user_id), str(event_type))
            allow_event = bool((payload or {}).get("allow_event", True))
            reason = str((payload or {}).get("reason") or ("ok" if allow_event else "blocked")).strip().lower()
            return allow_event, reason
        except Exception as exc:
            print(f"  [WARN] Presence gate check failed; allowing recognition ({exc})")
            return True, "gate_error"

    def _recognition_event_lock_seconds(self) -> float:
        return max(0.0, float(getattr(self.config, "recognition_event_lock_seconds", 8) or 8.0))

    def _prune_expired_recognition_locks(self, now: float, lock_seconds: float) -> None:
        if lock_seconds <= 0 or not self._recognition_event_locks:
            return
        cutoff = float(now) - float(lock_seconds)
        stale_keys = [
            key for key, ts in self._recognition_event_locks.items()
            if float(ts) < cutoff
        ]
        for key in stale_keys:
            self._recognition_event_locks.pop(key, None)

    def _should_emit_recognition_event(self, user_id: int) -> bool:
        now = time.time()
        event_type = self._event_type_for_current_repository()
        lock_seconds = self._recognition_event_lock_seconds()
        self._prune_expired_recognition_locks(now, lock_seconds)
        if lock_seconds <= 0:
            return True

        lock_key = (int(user_id), event_type)
        last_emitted_at = self._recognition_event_locks.get(lock_key)
        if last_emitted_at is not None and (now - float(last_emitted_at)) < lock_seconds:
            return False

        self._recognition_event_locks[lock_key] = now
        return True

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

    def _recognition_thresholds(self) -> tuple[float, float, float]:
        base_threshold = float(self.state.base_threshold)
        primary_threshold = max(float(self.config.primary_threshold), base_threshold)
        secondary_threshold = max(float(self.config.secondary_threshold), base_threshold)
        return primary_threshold, secondary_threshold, base_threshold

    def _empty_match_diagnostics(self) -> dict[str, Any]:
        primary_threshold, secondary_threshold, base_threshold = self._recognition_thresholds()
        return {
            "primary_model": self.config.primary_model,
            "secondary_model": self.config.secondary_model,
            "primary_confidence": None,
            "secondary_confidence": None,
            "average_confidence": None,
            "primary_distance": None,
            "secondary_distance": None,
            "average_distance": None,
            "primary_threshold": primary_threshold,
            "secondary_threshold": secondary_threshold,
            "base_threshold": base_threshold,
            "recognition_threshold": float(self.config.recognition_confidence_threshold),
            "primary_pass": False,
            "secondary_pass": False,
            "two_factor_pass": False,
            "candidate_name": None,
        }

    def _build_match_diagnostics(
        self,
        user_name: str,
        primary_confidence: float,
        secondary_confidence: float,
        primary_distance: float,
        secondary_distance: float,
    ) -> dict[str, Any]:
        primary_threshold, secondary_threshold, base_threshold = self._recognition_thresholds()
        average_confidence = (float(primary_confidence) + float(secondary_confidence)) / 2
        average_distance = (float(primary_distance) + float(secondary_distance)) / 2
        primary_pass = float(primary_confidence) >= primary_threshold
        secondary_pass = float(secondary_confidence) >= secondary_threshold
        return {
            "primary_model": self.config.primary_model,
            "secondary_model": self.config.secondary_model,
            "primary_confidence": float(primary_confidence),
            "secondary_confidence": float(secondary_confidence),
            "average_confidence": average_confidence,
            "primary_distance": float(primary_distance),
            "secondary_distance": float(secondary_distance),
            "average_distance": average_distance,
            "primary_threshold": primary_threshold,
            "secondary_threshold": secondary_threshold,
            "base_threshold": base_threshold,
            "recognition_threshold": float(self.config.recognition_confidence_threshold),
            "primary_pass": primary_pass,
            "secondary_pass": secondary_pass,
            "two_factor_pass": primary_pass and secondary_pass,
            "candidate_name": str(user_name or "").strip() or None,
        }

    @staticmethod
    def _format_confidence_diagnostics(diagnostics: dict[str, Any]) -> str:
        def _pct(value: Any) -> str:
            try:
                return f"{float(value):.2%}"
            except Exception:
                return "n/a"

        primary_model = str(diagnostics.get("primary_model") or "primary")
        secondary_model = str(diagnostics.get("secondary_model") or "secondary")
        return (
            f"{primary_model}={_pct(diagnostics.get('primary_confidence'))}/"
            f"{_pct(diagnostics.get('primary_threshold'))}, "
            f"{secondary_model}={_pct(diagnostics.get('secondary_confidence'))}/"
            f"{_pct(diagnostics.get('secondary_threshold'))}, "
            f"avg={_pct(diagnostics.get('average_confidence'))}, "
            f"base={_pct(diagnostics.get('base_threshold'))}"
        )

    def _model_confidence_display_enabled(self) -> bool:
        return bool(getattr(self.config, "cli_model_confidence_display_enabled", True))

    def find_best_match(self, query_embeddings) -> RecognitionResult | None:
        self._last_match_diagnostics = self._empty_match_diagnostics()
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
        best_candidate_diagnostics: dict[str, Any] | None = None

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

            candidate_diagnostics = self._build_match_diagnostics(
                user.name,
                primary_confidence,
                secondary_confidence,
                primary_best_dist,
                secondary_best_dist,
            )
            if (
                best_candidate_diagnostics is None
                or float(candidate_diagnostics["average_confidence"])
                > float(best_candidate_diagnostics["average_confidence"])
            ):
                best_candidate_diagnostics = candidate_diagnostics

            primary_threshold = float(candidate_diagnostics["primary_threshold"])
            secondary_threshold = float(candidate_diagnostics["secondary_threshold"])
            primary_pass = bool(candidate_diagnostics["primary_pass"])
            secondary_pass = bool(candidate_diagnostics["secondary_pass"])

            if not (primary_pass and secondary_pass):
                continue

            avg_confidence = float(candidate_diagnostics["average_confidence"])
            avg_distance = float(candidate_diagnostics["average_distance"])

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
                self._last_match_diagnostics = candidate_diagnostics

        if best_match:
            if self._model_confidence_display_enabled():
                print(
                    f"  [OK] 2-Factor Verified: {best_match.user.name} "
                    f"({self._format_confidence_diagnostics(self._last_match_diagnostics)})"
                )
            else:
                print(f"  [OK] 2-Factor Verified: {best_match.user.name}")
        elif best_candidate_diagnostics is not None:
            self._last_match_diagnostics = best_candidate_diagnostics
            if self._model_confidence_display_enabled():
                candidate_name = best_candidate_diagnostics.get("candidate_name") or "unknown user"
                print(
                    f"  Best model candidate below gates: {candidate_name} "
                    f"({self._format_confidence_diagnostics(best_candidate_diagnostics)})"
                )

        return best_match

    def recognize(self, face_crop):
        embeddings = self.embedding_service.extract_embedding_ensemble(face_crop)
        if not embeddings:
            return None, {}, self._empty_match_diagnostics()
        match = self.find_best_match(embeddings)
        return match, embeddings, dict(self._last_match_diagnostics)

    def is_confident_recognition(self, match: RecognitionResult) -> bool:
        return float(match.confidence) >= float(self.config.recognition_confidence_threshold)

    def register_or_recognize_face(
        self,
        face_crop,
        quality_service,
        allow_registration: bool = False,
        detection_confidence=None,
        landmarks=None,
        precomputed_quality=None,
        quality_context: str = "entry",
        registration_quality=None,
    ):
        reg_state = self.state.registration_state
        model_confidences = self._empty_match_diagnostics()
        if reg_state.in_progress and allow_registration:
            return {
                "status": "registration_pending",
                "reason_code": "registration_pending",
                "model_confidences": model_confidences,
            }

        if precomputed_quality is None:
            quality_score, quality_status, quality_debug = quality_service.assess_face_quality(
                face_crop,
                detection_confidence=detection_confidence,
                landmarks=landmarks,
                context=quality_context,
            )
        else:
            quality_score, quality_status, quality_debug = precomputed_quality

        quality_threshold = self.config.quality_profile_for_context(quality_context).face_quality_threshold
        if quality_score < quality_threshold:
            if allow_registration and reg_state.phase == "capturing":
                if registration_quality is None:
                    registration_quality = quality_service.assess_face_quality(
                        face_crop,
                        detection_confidence=detection_confidence,
                        landmarks=landmarks,
                        context="registration",
                    )
                reg_quality_score, reg_quality_status, reg_quality_debug = registration_quality
                registration_threshold = self.config.quality_profile_for_context("registration").face_quality_threshold
                if reg_quality_score >= registration_threshold:
                    quality_score, quality_status, quality_debug = reg_quality_score, reg_quality_status, reg_quality_debug
                else:
                    message = f"  Skipping low quality registration sample: {reg_quality_score:.2f} ({reg_quality_status})"
                    if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
                        primary_issue = reg_quality_debug.get("primary_issue_label")
                        if primary_issue:
                            message += f" | main issue: {primary_issue}"
                    if self.config.quality_debug_enabled and self.config.quality_debug_show_all_scores:
                        message += f" | {quality_service.quality_debug_summary(reg_quality_debug)}"
                    print(message)
                    return {
                        "status": "low_quality",
                        "reason_code": "low_quality",
                        "quality_score": reg_quality_score,
                        "quality_status": reg_quality_status,
                        "quality_debug": reg_quality_debug,
                        "match_confidence": None,
                        "match_threshold": None,
                        "model_confidences": model_confidences,
                    }
            else:
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
                    "reason_code": "low_quality",
                    "quality_score": quality_score,
                    "quality_status": quality_status,
                    "quality_debug": quality_debug,
                    "match_confidence": None,
                    "match_threshold": None,
                    "model_confidences": model_confidences,
                }

        registration_capture_active = bool(allow_registration and reg_state.phase == "capturing")
        current_pose = None
        detected_pose = None
        if registration_capture_active:
            if registration_quality is None:
                registration_quality = quality_service.assess_face_quality(
                    face_crop,
                    detection_confidence=detection_confidence,
                    landmarks=landmarks,
                    context="registration",
                )
            reg_quality_score, reg_quality_status, reg_quality_debug = registration_quality
            registration_threshold = self.config.quality_profile_for_context("registration").face_quality_threshold
            if reg_quality_score < registration_threshold:
                message = f"  Skipping low quality registration sample: {reg_quality_score:.2f} ({reg_quality_status})"
                if self.config.quality_debug_enabled and self.config.quality_debug_show_primary_issue:
                    primary_issue = reg_quality_debug.get("primary_issue_label")
                    if primary_issue:
                        message += f" | main issue: {primary_issue}"
                if self.config.quality_debug_enabled and self.config.quality_debug_show_all_scores:
                    message += f" | {quality_service.quality_debug_summary(reg_quality_debug)}"
                print(message)
                return {
                    "status": "low_quality",
                    "reason_code": "low_quality",
                    "quality_score": reg_quality_score,
                    "quality_status": reg_quality_status,
                    "quality_debug": reg_quality_debug,
                    "match_confidence": None,
                    "match_threshold": None,
                    "model_confidences": model_confidences,
                }

            current_pose = self.state.get_current_registration_pose() or "front"
            detected_pose = quality_service.detect_face_pose(face_crop, landmarks=landmarks)
            if detected_pose != current_pose:
                pose_label = detected_pose or "unknown"
                print(
                    f"  Skipped registration sample: expected pose '{current_pose}', detected '{pose_label}'"
                )
                return {
                    "status": "pose_mismatch",
                    "reason_code": "pose_mismatch",
                    "quality_score": reg_quality_score,
                    "quality_status": reg_quality_status,
                    "quality_debug": reg_quality_debug,
                    "match_confidence": None,
                    "match_threshold": None,
                    "model_confidences": model_confidences,
                    "expected_pose": current_pose,
                    "detected_pose": detected_pose,
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

        best_match, embeddings, model_confidences = self.recognize(face_crop)
        if not embeddings:
            print("  Failed to extract embeddings")
            return {
                "status": "embedding_failed",
                "reason_code": "embedding_failed",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": None,
                "match_threshold": None,
                "model_confidences": model_confidences,
            }

        if best_match:
            if not self.is_confident_recognition(best_match):
                print(
                    f"  Recognition below confidence threshold: "
                    f"{best_match.confidence:.2%} < {self.config.recognition_confidence_threshold:.2%}"
                )
                return {
                    "status": "uncertain",
                    "reason_code": "uncertain_match",
                    "quality_score": quality_score,
                    "quality_status": quality_status,
                    "quality_debug": quality_debug,
                    "match_confidence": best_match.confidence,
                    "match_threshold": max(best_match.threshold, self.config.recognition_confidence_threshold),
                    "model_confidences": model_confidences,
                }

            event_type = self._event_type_for_current_repository()
            gate_allowed, gate_reason = self._presence_gate_allows_event(int(best_match.user_id), event_type)
            if not gate_allowed:
                recognized_payload = recognized_user_payload(best_match)
                self.state.set_recognized_user(recognized_payload)
                print(
                    f"  Skipped recognition for {best_match.user.name}: "
                    f"presence gate blocked ({gate_reason})"
                )
                return {
                    "status": "blocked",
                    "reason_code": gate_reason or "presence_gate_blocked",
                    "quality_score": quality_score,
                    "quality_status": quality_status,
                    "quality_debug": quality_debug,
                    "match_confidence": best_match.confidence,
                    "match_threshold": max(best_match.threshold, self.config.recognition_confidence_threshold),
                    "model_confidences": model_confidences,
                    "payload": recognized_payload,
                }

            if self._should_emit_recognition_event(int(best_match.user_id)):
                self.repository.log_recognition(best_match, face_quality=quality_score, method="two-factor")
            else:
                print(
                    f"  Skipped duplicate recognition event for {best_match.user.name} "
                    f"(lock={float(getattr(self.config, 'recognition_event_lock_seconds', 8)):.1f}s)"
                )

            learning_threshold = float(getattr(self.config, "online_learning_confidence_threshold", 0.90) or 0.90)
            if float(best_match.confidence) >= learning_threshold:
                primary_new = first_embedding(embeddings, self.config.primary_model)
                secondary_new = first_embedding(embeddings, self.config.secondary_model)
                if primary_new is not None:
                    best_match.user.embeddings.setdefault(self.config.primary_model, []).append(primary_new)
                if secondary_new is not None:
                    best_match.user.embeddings.setdefault(self.config.secondary_model, []).append(secondary_new)

                updated_user = self.repository.update_embeddings(best_match.user_id, embeddings, image_path=None)
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
            else:
                print(
                    f"  Skipped online learning for {best_match.user.name}: "
                    f"{best_match.confidence:.2%} < {learning_threshold:.2%}"
                )

            recognized_payload = recognized_user_payload(best_match)
            self.state.set_recognized_user(recognized_payload)
            print(
                f"[OK] Recognized: {recognized_payload['name']} "
                f"(conf: {best_match.confidence:.2%}, dist: {best_match.distance:.4f})"
            )
            return {
                "status": "recognized",
                "reason_code": "recognized_existing",
                "quality_score": quality_score,
                "quality_status": quality_status,
                "quality_debug": quality_debug,
                "match_confidence": best_match.confidence,
                "match_threshold": max(best_match.threshold, self.config.recognition_confidence_threshold),
                "model_confidences": model_confidences,
                "payload": recognized_payload,
            }

        if registration_capture_active:
            reg_quality_score, reg_quality_status, reg_quality_debug = registration_quality

            sample = RegistrationSample(
                face_crop=face_crop,
                embeddings=embeddings,
                quality=reg_quality_score,
                pose=current_pose,
            )
            print(f"  Captured registration sample for pose '{current_pose}'")

            return {
                "status": "registration_captured",
                "reason_code": "registration_captured",
                "quality_score": reg_quality_score,
                "quality_status": reg_quality_status,
                "quality_debug": reg_quality_debug,
                "match_confidence": None,
                "match_threshold": None,
                "model_confidences": model_confidences,
                "expected_pose": current_pose,
                "detected_pose": detected_pose,
                "registration_sample": sample,
            }

        return {
            "status": "no_match",
            "reason_code": "no_match",
            "quality_score": quality_score,
            "quality_status": quality_status,
            "quality_debug": quality_debug,
            "match_confidence": None,
            "match_threshold": None,
            "model_confidences": model_confidences,
        }
