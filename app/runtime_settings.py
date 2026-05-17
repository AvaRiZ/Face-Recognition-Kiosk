from __future__ import annotations

import os
from pathlib import Path

from core.config import AppConfig, QUALITY_CONTEXTS, QUALITY_PROFILE_BOUNDS, QUALITY_PROFILE_FIELDS
from core.state import AppStateManager
from db import get_app_setting


def load_env_file_if_present(file_path: Path) -> None:
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def load_default_local_env(repo_root: Path) -> None:
    load_env_file_if_present(repo_root / ".env.local")


def _coerce_float(raw_value, fallback, minimum, maximum):
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        parsed = float(fallback)
    return max(float(minimum), min(float(maximum), float(parsed)))


def _coerce_int(raw_value, fallback, minimum, maximum):
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(int(minimum), min(int(maximum), int(parsed)))


def _coerce_bool(raw_value, fallback):
    if isinstance(raw_value, bool):
        return raw_value
    text = str(raw_value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(fallback)


def _quality_setting_key(context, field_name):
    return f"{context}_quality_{field_name}"


def _coerce_quality_value(field_name, raw_value, fallback):
    try:
        return int(raw_value) if field_name in {"quality_face_area_min", "quality_face_area_good"} else float(raw_value)
    except (TypeError, ValueError):
        return fallback


def _apply_face_quality_profiles(config: AppConfig, legacy_quality_threshold: float) -> None:
    profiles = {}
    for context in QUALITY_CONTEXTS:
        base = config.quality_profile_for_context(context).to_dict()
        for field_name in QUALITY_PROFILE_FIELDS:
            fallback = legacy_quality_threshold if field_name == "face_quality_threshold" else base[field_name]
            raw_value = get_app_setting(config.db_path, _quality_setting_key(context, field_name), str(fallback))
            parsed = _coerce_quality_value(field_name, raw_value, fallback)
            bounds = QUALITY_PROFILE_BOUNDS[field_name]
            base[field_name] = max(bounds["min"], min(bounds["max"], parsed))
        profiles[context] = base
    config.apply_quality_profiles(profiles)


def apply_app_settings(config: AppConfig, state: AppStateManager) -> None:
    threshold = _coerce_float(
        get_app_setting(config.db_path, "threshold", str(state.base_threshold)),
        state.base_threshold,
        0.1,
        0.95,
    )
    config.primary_threshold = _coerce_float(
        get_app_setting(config.db_path, "primary_threshold", str(config.primary_threshold)),
        config.primary_threshold,
        0.1,
        0.95,
    )
    config.secondary_threshold = _coerce_float(
        get_app_setting(config.db_path, "secondary_threshold", str(config.secondary_threshold)),
        config.secondary_threshold,
        0.1,
        0.95,
    )
    quality_threshold = _coerce_float(
        get_app_setting(config.db_path, "quality_threshold", str(state.face_quality_threshold)),
        state.face_quality_threshold,
        0.1,
        0.95,
    )
    state.set_thresholds(threshold, quality_threshold)
    _apply_face_quality_profiles(config, quality_threshold)

    config.recognition_confidence_threshold = _coerce_float(
        get_app_setting(
            config.db_path,
            "recognition_confidence_threshold",
            str(config.recognition_confidence_threshold),
        ),
        config.recognition_confidence_threshold,
        0.1,
        0.99,
    )
    config.online_learning_confidence_threshold = _coerce_float(
        get_app_setting(
            config.db_path,
            "online_learning_confidence_threshold",
            str(config.online_learning_confidence_threshold),
        ),
        config.online_learning_confidence_threshold,
        0.1,
        0.99,
    )
    config.vector_index_top_k = _coerce_int(
        get_app_setting(config.db_path, "vector_index_top_k", str(config.vector_index_top_k)),
        config.vector_index_top_k,
        1,
        100,
    )
    config.max_library_capacity = _coerce_int(
        get_app_setting(config.db_path, "max_occupancy", str(config.max_library_capacity)),
        config.max_library_capacity,
        50,
        2000,
    )
    config.occupancy_warning_threshold = _coerce_float(
        get_app_setting(
            config.db_path,
            "occupancy_warning_threshold",
            str(config.occupancy_warning_threshold),
        ),
        config.occupancy_warning_threshold,
        0.5,
        0.99,
    )
    config.occupancy_snapshot_interval_seconds = _coerce_int(
        get_app_setting(
            config.db_path,
            "occupancy_snapshot_interval_seconds",
            str(config.occupancy_snapshot_interval_seconds),
        ),
        config.occupancy_snapshot_interval_seconds,
        60,
        3600,
    )
    config.recognition_event_retention_days = _coerce_int(
        get_app_setting(
            config.db_path,
            "recognition_event_retention_days",
            str(getattr(config, "recognition_event_retention_days", 365)),
        ),
        getattr(config, "recognition_event_retention_days", 365),
        1,
        3650,
    )
    config.cli_model_confidence_display_enabled = _coerce_bool(
        get_app_setting(
            config.db_path,
            "cli_model_confidence_display_enabled",
            str(getattr(config, "cli_model_confidence_display_enabled", True)),
        ),
        getattr(config, "cli_model_confidence_display_enabled", True),
    )

    entry_source = str(
        get_app_setting(config.db_path, "entry_cctv_stream_source", str(config.entry_cctv_stream_source))
        or ""
    ).strip()
    if entry_source:
        config.entry_cctv_stream_source = entry_source

    exit_source = str(
        get_app_setting(config.db_path, "exit_cctv_stream_source", str(config.exit_cctv_stream_source))
        or ""
    ).strip()
    if exit_source:
        config.exit_cctv_stream_source = exit_source
