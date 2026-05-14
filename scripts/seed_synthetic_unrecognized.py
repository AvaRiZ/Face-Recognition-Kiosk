from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import connect as db_connect
from db import resolve_database_target


DEFAULT_BATCH_ID = "may-2026-unrecognized-v1"
DEFAULT_SEED = 20260514

TARGET_COUNTS = {
    date(2026, 5, 9): 28,
    date(2026, 5, 10): 22,
    date(2026, 5, 11): 44,
    date(2026, 5, 12): 55,
    date(2026, 5, 13): 28,
}

UNKNOWN_ID_NAMESPACE = uuid.UUID("4924ef7d-adf8-42d9-88e9-ff86d9f76a2f")


@dataclass(frozen=True)
class DayProfile:
    day: date
    target_count: int
    first_real: datetime
    last_real: datetime
    real_events: tuple[tuple[datetime, str], ...]
    real_entries: int
    real_exits: int


@dataclass(frozen=True)
class SyntheticEvent:
    event_id: str
    day: date
    event_type: str
    camera_id: int
    captured_at: datetime
    ingested_at: datetime
    confidence: float
    face_quality: float
    match_threshold: float
    payload: dict


def _load_env_file_if_present(file_path: Path) -> None:
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


def _resolve_db_target(db_arg: str | None) -> str:
    _load_env_file_if_present(ROOT / ".env.local")
    if db_arg:
        os.environ["DATABASE_URL"] = str(db_arg).strip()

    target = resolve_database_target(db_arg)
    if not target:
        raise RuntimeError("DATABASE_URL is required, or pass --db.")
    return target


def _normalize_event_type(value: object) -> str:
    return "exit" if str(value or "").strip().lower() == "exit" else "entry"


def _read_day_profiles(db_target: str) -> list[DayProfile]:
    conn = db_connect(db_target)
    try:
        profiles: list[DayProfile] = []
        with conn.cursor() as cursor:
            for day, target_count in TARGET_COUNTS.items():
                cursor.execute(
                    """
                    SELECT
                        COALESCE(captured_at, ingested_at) AS event_time,
                        COALESCE(NULLIF(TRIM(event_type), ''), 'entry') AS event_type
                    FROM recognition_events
                    WHERE DATE(COALESCE(captured_at, ingested_at)) = %s
                      AND LOWER(COALESCE(NULLIF(TRIM(decision), ''), 'allowed')) <> 'unknown'
                    ORDER BY event_time ASC, id ASC
                    """,
                    (day.isoformat(),),
                )
                rows = cursor.fetchall()
                if not rows:
                    raise RuntimeError(
                        f"No real recognition events found for {day.isoformat()}; "
                        "cannot infer a natural time window."
                    )

                real_events = tuple(
                    (event_time, _normalize_event_type(event_type))
                    for event_time, event_type in rows
                    if isinstance(event_time, datetime)
                )
                if not real_events:
                    raise RuntimeError(f"No timestamped real recognition events found for {day.isoformat()}.")

                real_entries = sum(1 for _, event_type in real_events if event_type == "entry")
                real_exits = sum(1 for _, event_type in real_events if event_type == "exit")
                profiles.append(
                    DayProfile(
                        day=day,
                        target_count=int(target_count),
                        first_real=real_events[0][0],
                        last_real=real_events[-1][0],
                        real_events=real_events,
                        real_entries=real_entries,
                        real_exits=real_exits,
                    )
                )
        return profiles
    finally:
        conn.close()


def _stable_seed(seed: int, batch_id: str, day: date) -> int:
    material = f"{seed}:{batch_id}:{day.isoformat()}"
    return uuid.uuid5(uuid.NAMESPACE_URL, material).int & ((1 << 64) - 1)


def _event_id_for(batch_id: str, day: date, ordinal: int) -> str:
    material = f"{batch_id}:{day.isoformat()}:{ordinal:04d}"
    return f"unknown-{uuid.uuid5(UNKNOWN_ID_NAMESPACE, material).hex}"


def _event_types_for(profile: DayProfile, rng: random.Random) -> list[str]:
    real_total = profile.real_entries + profile.real_exits
    if real_total <= 0:
        return ["entry"] * profile.target_count

    entry_count = int(round(profile.target_count * (profile.real_entries / real_total)))
    entry_count = max(0, min(profile.target_count, entry_count))
    exit_count = profile.target_count - entry_count

    if profile.real_entries > 0 and entry_count == 0 and profile.target_count > 1:
        entry_count = 1
        exit_count = profile.target_count - entry_count
    if profile.real_exits > 0 and exit_count == 0 and profile.target_count > 1:
        exit_count = 1
        entry_count = profile.target_count - exit_count

    event_types = (["entry"] * entry_count) + (["exit"] * exit_count)
    rng.shuffle(event_types)
    return event_types


def _clamp_timestamp(candidate: datetime, start: datetime, end: datetime, rng: random.Random) -> datetime:
    if end <= start:
        return start

    window_seconds = max(0.0, (end - start).total_seconds())
    edge_padding = min(30.0, window_seconds / 10.0)
    if candidate < start:
        return start + timedelta(seconds=rng.uniform(0.0, edge_padding))
    if candidate > end:
        return end - timedelta(seconds=rng.uniform(0.0, edge_padding))
    return candidate


def _sample_timestamp(profile: DayProfile, rng: random.Random) -> datetime:
    first_real = profile.first_real
    last_real = profile.last_real
    window_seconds = max(0.0, (last_real - first_real).total_seconds())
    if window_seconds <= 0:
        return first_real

    base_time = rng.choice(profile.real_events)[0]
    jitter_limit = min(300.0, max(20.0, window_seconds * 0.04))
    candidate = base_time + timedelta(seconds=rng.uniform(-jitter_limit, jitter_limit))
    candidate = _clamp_timestamp(candidate, first_real, last_real, rng)

    if candidate < first_real:
        return first_real
    if candidate > last_real:
        return last_real
    return candidate


def _build_payload(event: SyntheticEvent, batch_id: str, ordinal: int) -> dict:
    return {
        "event_id": event.event_id,
        "camera_id": event.camera_id,
        "event_type": event.event_type,
        "user_id": None,
        "sr_code": None,
        "decision": "unknown",
        "identity_user_type": "unrecognized",
        "identity_name": "Unrecognized User",
        "identity_sr_code": "",
        "track_id": None,
        "snapshot_metadata": {
            "synthetic": True,
            "synthetic_batch_id": batch_id,
            "synthetic_sequence": ordinal,
        },
        "confidence": event.confidence,
        "match_threshold": event.match_threshold,
        "face_quality": event.face_quality,
        "method": "immediate-unrecognized",
        "captured_at": event.captured_at.isoformat(),
        "ingested_at": event.ingested_at.isoformat(),
        "synthetic": True,
        "synthetic_batch_id": batch_id,
    }


def _build_synthetic_events(profiles: list[DayProfile], *, seed: int, batch_id: str) -> list[SyntheticEvent]:
    events: list[SyntheticEvent] = []

    for profile in profiles:
        rng = random.Random(_stable_seed(seed, batch_id, profile.day))
        event_types = _event_types_for(profile, rng)

        for ordinal, event_type in enumerate(event_types, start=1):
            captured_at = _sample_timestamp(profile, rng)
            ingested_at = captured_at + timedelta(seconds=rng.uniform(0.5, 3.5))
            camera_id = 2 if event_type == "exit" else 1
            # Keep the original RNG sequence stable while storing zero confidence.
            rng.uniform(0.18, 0.68)
            event = SyntheticEvent(
                event_id=_event_id_for(batch_id, profile.day, ordinal),
                day=profile.day,
                event_type=event_type,
                camera_id=camera_id,
                captured_at=captured_at,
                ingested_at=ingested_at,
                confidence=0.0,
                face_quality=round(rng.uniform(0.80, 0.96), 4),
                match_threshold=0.72,
                payload={},
            )
            events.append(
                SyntheticEvent(
                    **{
                        **event.__dict__,
                        "payload": _build_payload(event, batch_id, ordinal),
                    }
                )
            )

    return sorted(events, key=lambda item: (item.captured_at, item.event_id))


def _batch_marker_patterns(batch_id: str) -> tuple[str, str]:
    escaped = batch_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return (
        f'%\"synthetic_batch_id\": \"{escaped}\"%',
        f'%\"synthetic_batch_id\":\"{escaped}\"%',
    )


def _synthetic_true_filter() -> str:
    return """
    (
        COALESCE(payload_json, '') LIKE '%%"synthetic": true%%'
        OR COALESCE(payload_json, '') LIKE '%%"synthetic":true%%'
    )
    """


def _batch_filter_sql() -> str:
    return f"""
    LOWER(COALESCE(NULLIF(TRIM(decision), ''), 'allowed')) = 'unknown'
    AND {_synthetic_true_filter()}
    AND (
        COALESCE(payload_json, '') LIKE %s ESCAPE '\\'
        OR COALESCE(payload_json, '') LIKE %s ESCAPE '\\'
    )
    """


def _count_existing_batch(cursor, batch_id: str) -> tuple[int, datetime | None, datetime | None]:
    cursor.execute(
        f"""
        SELECT
            COUNT(*),
            MIN(COALESCE(captured_at, ingested_at)),
            MAX(COALESCE(captured_at, ingested_at))
        FROM recognition_events
        WHERE {_batch_filter_sql()}
        """,
        _batch_marker_patterns(batch_id),
    )
    row = cursor.fetchone() or (0, None, None)
    return int(row[0] or 0), row[1], row[2]


def _delete_existing_batch(cursor, batch_id: str) -> int:
    cursor.execute(
        f"""
        DELETE FROM recognition_events
        WHERE {_batch_filter_sql()}
        """,
        _batch_marker_patterns(batch_id),
    )
    return int(cursor.rowcount or 0)


def _recalculate_daily_occupancy_state(cursor, days: list[date]) -> dict[str, tuple[int, int]]:
    recalculated: dict[str, tuple[int, int]] = {}
    updated_at = datetime.now(timezone.utc)
    for day in days:
        state_date = day.isoformat()
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) AS entries,
                SUM(CASE WHEN event_type = 'exit' THEN 1 ELSE 0 END) AS exits
            FROM recognition_events
            WHERE DATE(COALESCE(captured_at, ingested_at)) = %s
              AND LOWER(COALESCE(NULLIF(TRIM(decision), ''), 'allowed')) IN ('allowed', 'unknown')
              AND COALESCE(payload_json, '') NOT LIKE '%%"revoked": true%%'
              AND COALESCE(payload_json, '') NOT LIKE '%%"revoked":true%%'
            """,
            (state_date,),
        )
        entries, exits = cursor.fetchone() or (0, 0)
        entries = int(entries or 0)
        exits = int(exits or 0)
        cursor.execute(
            """
            INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(state_date) DO UPDATE SET
                daily_entries = EXCLUDED.daily_entries,
                daily_exits = EXCLUDED.daily_exits,
                updated_at = EXCLUDED.updated_at
            """,
            (state_date, entries, exits, updated_at),
        )
        recalculated[state_date] = (entries, exits)
    return recalculated


def _insert_events(cursor, events: list[SyntheticEvent]) -> int:
    inserted = 0
    for event in events:
        cursor.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence,
                primary_confidence, secondary_confidence, primary_distance, secondary_distance,
                face_quality, method, captured_at, ingested_at, payload_json
            )
            VALUES (%s, NULL, NULL, 'unknown', %s, %s, NULL, NULL, NULL, NULL, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event.event_id,
                event.event_type,
                event.confidence,
                event.face_quality,
                "immediate-unrecognized",
                event.captured_at,
                event.ingested_at,
                json.dumps(event.payload, ensure_ascii=True),
            ),
        )
        inserted += int(cursor.rowcount or 0)
    return inserted


def _print_insert_preview(profiles: list[DayProfile], events: list[SyntheticEvent], batch_count: int) -> None:
    print(f"Synthetic batch already present: {batch_count} row(s)")
    print("Insert preview:")
    for profile in profiles:
        day_events = [event for event in events if event.day == profile.day]
        entries = sum(1 for event in day_events if event.event_type == "entry")
        exits = sum(1 for event in day_events if event.event_type == "exit")
        first = min(event.captured_at for event in day_events)
        last = max(event.captured_at for event in day_events)
        print(
            "  "
            f"{profile.day.isoformat()}: {len(day_events)} synthetic "
            f"({entries} entry, {exits} exit), real window "
            f"{profile.first_real.isoformat()} -> {profile.last_real.isoformat()}, "
            f"synthetic span {first.isoformat()} -> {last.isoformat()}"
        )
    if events:
        sample_ids = ", ".join(event.event_id for event in events[:3])
        print(f"Sample canonical unknown IDs: {sample_ids}")
    print(f"Total planned inserts: {len(events)}")


def _print_recalculated_state(recalculated: dict[str, tuple[int, int]]) -> None:
    print("Recalculated daily occupancy state:")
    for state_date, (entries, exits) in sorted(recalculated.items()):
        print(f"  {state_date}: {entries} entries, {exits} exits")


def _validate_batch_id(value: str) -> str:
    text = str(value or "").strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not text or any(char not in allowed for char in text):
        raise argparse.ArgumentTypeError(
            "--batch-id may contain only letters, numbers, dot, underscore, and hyphen."
        )
    return text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed canonical synthetic unrecognized recognition events for May 9-13, 2026."
    )
    parser.add_argument("--db", default=None, help="PostgreSQL DSN target (defaults to DATABASE_URL or .env.local).")
    parser.add_argument("--apply", action="store_true", help="Apply the selected operation. Defaults to dry-run.")
    parser.add_argument("--rollback", action="store_true", help="Delete the selected synthetic batch instead of inserting.")
    parser.add_argument("--replace", action="store_true", help="Delete the selected synthetic batch before inserting it.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Deterministic generation seed. Default: {DEFAULT_SEED}.")
    parser.add_argument(
        "--batch-id",
        type=_validate_batch_id,
        default=DEFAULT_BATCH_ID,
        help=f"Synthetic batch marker used for insertion and rollback. Default: {DEFAULT_BATCH_ID}.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.rollback and args.replace:
        parser.error("--rollback and --replace cannot be used together.")

    db_target = _resolve_db_target(args.db)
    target_days = sorted(TARGET_COUNTS)

    if args.rollback:
        conn = db_connect(db_target)
        try:
            with conn.cursor() as cursor:
                batch_count, first_seen, last_seen = _count_existing_batch(cursor, args.batch_id)
            print(f"Rollback target batch: {args.batch_id}")
            print(f"Rows matching synthetic batch marker: {batch_count}")
            if first_seen and last_seen:
                print(f"Matching timestamp span: {first_seen.isoformat()} -> {last_seen.isoformat()}")
        finally:
            conn.close()

        if not args.apply:
            print("Dry-run only. Re-run with --rollback --apply to delete these synthetic rows.")
            return

        conn = db_connect(db_target)
        try:
            with conn:
                with conn.cursor() as cursor:
                    deleted = _delete_existing_batch(cursor, args.batch_id)
                    recalculated = _recalculate_daily_occupancy_state(cursor, target_days)
            print(f"Deleted synthetic rows: {deleted}")
            _print_recalculated_state(recalculated)
            return
        finally:
            conn.close()

    profiles = _read_day_profiles(db_target)
    events = _build_synthetic_events(profiles, seed=int(args.seed), batch_id=args.batch_id)

    conn = db_connect(db_target)
    try:
        with conn.cursor() as cursor:
            batch_count, _first_seen, _last_seen = _count_existing_batch(cursor, args.batch_id)
    finally:
        conn.close()

    if args.replace:
        print(f"Replace requested: existing batch rows to remove first: {batch_count}")
    _print_insert_preview(profiles, events, batch_count)

    if not args.apply:
        action = "--replace --apply" if args.replace else "--apply"
        print(f"Dry-run only. Re-run with {action} to write the synthetic rows.")
        return

    conn = db_connect(db_target)
    try:
        with conn:
            with conn.cursor() as cursor:
                deleted = _delete_existing_batch(cursor, args.batch_id) if args.replace else 0
                inserted = _insert_events(cursor, events)
                recalculated = _recalculate_daily_occupancy_state(cursor, target_days)
        if args.replace:
            print(f"Deleted existing synthetic rows before insert: {deleted}")
        print(f"Inserted synthetic rows: {inserted}")
        print(f"Skipped duplicate rows: {len(events) - inserted}")
        _print_recalculated_state(recalculated)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
