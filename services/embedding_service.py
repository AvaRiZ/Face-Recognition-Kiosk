from __future__ import annotations

import cv2
import numpy as np

from core.config import AppConfig


def _normalize_embedding_list(value):
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        if value.ndim == 1:
            return [value.astype(np.float32, copy=False)]
        if value.ndim == 2:
            return [row.astype(np.float32, copy=False) for row in value]
        return []
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return []
        first = value[0]
        if isinstance(first, np.ndarray):
            return [v.astype(np.float32, copy=False) for v in value if isinstance(v, np.ndarray)]
        if isinstance(first, (list, tuple, np.ndarray)):
            out = []
            for v in value:
                arr = v if isinstance(v, np.ndarray) else np.array(v, dtype=np.float32)
                if arr.ndim == 1:
                    out.append(arr.astype(np.float32, copy=False))
                elif arr.ndim == 2:
                    out.extend([row.astype(np.float32, copy=False) for row in arr])
            return out
        if isinstance(first, (int, float, np.floating, np.integer)):
            arr = np.array(value, dtype=np.float32)
            if arr.ndim == 1:
                return [arr]
            return []
    if isinstance(value, (int, float, np.floating, np.integer)):
        return [np.array([value], dtype=np.float32)]
    return []


def normalize_embeddings_by_model(embeddings_by_model):
    if embeddings_by_model is None:
        return {}
    if not isinstance(embeddings_by_model, dict):
        return {}
    normalized = {}
    for model_name, value in embeddings_by_model.items():
        normalized[model_name] = _normalize_embedding_list(value)
    return normalized


def merge_embeddings_by_model(existing, new):
    merged = {}
    for model_name, emb_list in normalize_embeddings_by_model(existing).items():
        merged[model_name] = list(emb_list)
    for model_name, emb_list in normalize_embeddings_by_model(new).items():
        if model_name not in merged:
            merged[model_name] = []
        merged[model_name].extend(list(emb_list))
    return merged


def infer_embedding_dim(embeddings_by_model):
    if not embeddings_by_model:
        return 0
    normalized = normalize_embeddings_by_model(embeddings_by_model)
    for emb_list in normalized.values():
        if emb_list:
            emb = emb_list[0]
            if isinstance(emb, np.ndarray) and emb.ndim == 1:
                return emb.shape[0]
            if isinstance(emb, (list, tuple)):
                return len(emb)
    return 0


def count_embeddings(embeddings_by_model):
    if not embeddings_by_model:
        return 0
    normalized = normalize_embeddings_by_model(embeddings_by_model)
    return sum(len(v) for v in normalized.values() if v)


def first_embedding(embeddings_by_model, model_name: str):
    model_embeddings = embeddings_by_model.get(model_name)
    if not model_embeddings:
        return None
    if isinstance(model_embeddings, list) and model_embeddings:
        return model_embeddings[0]
    return None


class EmbeddingService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._deepface = None

    def _get_deepface(self):
        if self._deepface is None:
            from deepface import DeepFace

            self._deepface = DeepFace
        return self._deepface

    def warm_up_models(self, logger=None) -> None:
        """Preload DeepFace and all configured embedding models."""
        face_rgb = np.zeros((160, 160, 3), dtype=np.uint8)
        deepface = self._get_deepface()

        for model_name in self.config.models:
            try:
                deepface.represent(
                    img_path=face_rgb,
                    model_name=model_name,
                    enforce_detection=False,
                    detector_backend="skip",
                    align=False,
                    normalization="base",
                )
                if logger:
                    logger(f"Warm-up complete for {model_name}")
            except Exception as exc:
                if logger:
                    logger(f"Warm-up warning for {model_name}: {exc}", status="WARN")
                else:
                    print(f"  [WARN] Warm-up warning for {model_name}: {exc}")

    def extract_embedding_ensemble(self, face_crop):
        try:
            deepface = self._get_deepface()

            if len(face_crop.shape) == 3 and face_crop.shape[2] == 3:
                face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
            else:
                if len(face_crop.shape) == 2:
                    face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_GRAY2RGB)
                else:
                    face_rgb = face_crop

            embeddings = {}

            for model_name in self.config.models:
                try:
                    embedding_obj = deepface.represent(
                        img_path=face_rgb,
                        model_name=model_name,
                        enforce_detection=False,
                        detector_backend="skip",
                        # "skip" bypasses detector landmarks, so alignment is not available.
                        align=False,
                        normalization="base",
                    )

                    embedding = np.array(embedding_obj[0]["embedding"], dtype=np.float32)
                    norm = np.linalg.norm(embedding)
                    if norm > 0:
                        embedding = embedding / norm

                    embeddings[model_name] = [embedding]
                    print(f"  [OK] {model_name} embedding extracted")
                except Exception as exc:
                    print(f"  [WARN] {model_name} failed: {exc}")

            return embeddings
        except Exception as exc:
            print(f"Embedding extraction error: {exc}")
            return {}
