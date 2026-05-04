from __future__ import annotations

import argparse
import json
import os
import pickle
import sqlite3
import uuid
from typing import Iterable

import numpy as np
import psycopg
from psycopg import sql


TABLES = [
    "users",
    "staff_accounts",
    "recognition_events",
    "app_settings",
    "audit_log",
    "imported_logs",
]


def _normalize_postgres_url(url: str) -> str:
    normalized = url.strip()
    if "://" not in normalized:
        return normalized
    scheme, rest = normalized.split("://", 1)
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    if scheme == "postgres":
        scheme = "postgresql"
    return f"{scheme}://{rest}"


def _table_exists_sqlite(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _table_exists_postgres(conn: psycopg.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=%s
            )
            """,
            (table,),
        )
        return bool(cur.fetchone()[0])


def _fetch_sqlite_rows(conn: sqlite3.Connection, table: str):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    return columns, rows


def _count_sqlite_rows(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    row = cur.fetchone()
    return int(row[0] or 0)


def _copy_rows_postgres(conn: psycopg.Connection, table: str, columns: list[str], rows: Iterable[tuple]):
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    col_sql = ", ".join(columns)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def _reset_postgres_sequences(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
                            AND (
                                    column_default LIKE 'nextval(%'
                                    OR is_identity = 'YES'
                            )
            """,
            (table,),
        )
        serial_columns = [row[0] for row in cur.fetchall()]

    if not serial_columns:
        return

    with conn.cursor() as cur:
        for column in serial_columns:
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, column))
            sequence_name = cur.fetchone()[0]
            if not sequence_name:
                continue

            cur.execute(
                sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}")
                .format(sql.Identifier(column), sql.Identifier(table))
            )
            max_value = int(cur.fetchone()[0] or 0)
            if max_value > 0:
                cur.execute("SELECT setval(%s, %s, true)", (sequence_name, max_value))
            else:
                cur.execute("SELECT setval(%s, %s, false)", (sequence_name, 1))


def _normalize_legacy_embeddings(blob) -> dict[str, list[np.ndarray]]:
    if blob is None:
        return {}
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if not isinstance(blob, bytes) or not blob:
        return {}

    try:
        payload = pickle.loads(blob)
    except Exception:
        return {}

    def _normalize(values) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        if isinstance(values, np.ndarray):
            values = [values]
        if not isinstance(values, (list, tuple)):
            values = [values]
        for value in values:
            try:
                arr = np.asarray(value, dtype=np.float32)
            except Exception:
                continue
            if arr.ndim == 1 and arr.size > 0:
                out.append(arr)
            elif arr.ndim == 2:
                for row in arr:
                    if row.size > 0:
                        out.append(np.asarray(row, dtype=np.float32))
        return out

    if isinstance(payload, dict):
        out: dict[str, list[np.ndarray]] = {}
        for model_name, vectors in payload.items():
            normalized = _normalize(vectors)
            if normalized:
                out[str(model_name)] = normalized
        return out

    normalized = _normalize(payload)
    if normalized:
        return {"legacy": normalized}
    return {}


def _backfill_user_embeddings_from_users(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name='user_embeddings'
            )
            """
        )
        has_table = bool(cur.fetchone()[0])
    if not has_table:
        return 0

    with conn.cursor() as cur:
        cur.execute("SELECT user_id, embeddings FROM users WHERE embeddings IS NOT NULL")
        user_rows = cur.fetchall()

    inserted = 0
    for user_id, legacy_blob in user_rows:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM user_embeddings WHERE user_id=%s LIMIT 1", (int(user_id),))
            if cur.fetchone():
                continue

        embeddings_by_model = _normalize_legacy_embeddings(legacy_blob)
        if not embeddings_by_model:
            continue

        insert_rows = []
        for model_name, vectors in embeddings_by_model.items():
            for vector in vectors:
                insert_rows.append((int(user_id), str(model_name), vector.astype(np.float32, copy=False).tobytes()))

        if not insert_rows:
            continue

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO user_embeddings (user_id, model_name, embedding)
                VALUES (%s, %s, %s)
                """,
                insert_rows,
            )
        inserted += len(insert_rows)

    return inserted


def _backfill_legacy_recognition_log_into_events(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection) -> int:
    if not _table_exists_sqlite(sqlite_conn, "recognition_log"):
        return 0
    if not _table_exists_postgres(pg_conn, "recognition_events"):
        return 0
    if _table_exists_sqlite(sqlite_conn, "recognition_events") and _count_sqlite_rows(sqlite_conn, "recognition_events") > 0:
        return 0

    columns, rows = _fetch_sqlite_rows(sqlite_conn, "recognition_log")
    if not rows:
        return 0

    col_index = {name: idx for idx, name in enumerate(columns)}
    has_users = _table_exists_sqlite(sqlite_conn, "users")
    user_sr_map: dict[int, str] = {}
    if has_users:
        cur = sqlite_conn.cursor()
        try:
            cur.execute("SELECT user_id, sr_code FROM users")
            for raw_user_id, raw_sr_code in cur.fetchall():
                try:
                    uid = int(raw_user_id)
                except Exception:
                    continue
                user_sr_map[uid] = str(raw_sr_code or "")
        except Exception:
            user_sr_map = {}

    def _value(row: tuple, key: str):
        idx = col_index.get(key)
        if idx is None:
            return None
        return row[idx]

    insert_rows = []
    for row in rows:
        raw_log_id = _value(row, "log_id")
        try:
            log_id = int(raw_log_id) if raw_log_id is not None else None
        except Exception:
            log_id = None
        raw_user_id = _value(row, "user_id")
        try:
            user_id = int(raw_user_id) if raw_user_id is not None else None
        except Exception:
            user_id = None
        sr_code = user_sr_map.get(int(user_id), "") if user_id is not None else ""
        captured_at = _value(row, "timestamp")
        event_id = f"legacy-log-{log_id}" if log_id is not None else f"legacy-log-{uuid.uuid4().hex}"
        payload_json = json.dumps({"source": "sqlite_recognition_log", "legacy_log_id": log_id}, ensure_ascii=True)
        insert_rows.append(
            (
                event_id,
                user_id,
                sr_code or None,
                "allowed",
                "entry",
                _value(row, "confidence"),
                _value(row, "primary_confidence"),
                _value(row, "secondary_confidence"),
                _value(row, "primary_distance"),
                _value(row, "secondary_distance"),
                _value(row, "face_quality"),
                _value(row, "method") or "two-factor",
                captured_at,
                payload_json,
            )
        )

    if not insert_rows:
        return 0

    with pg_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence,
                primary_confidence, secondary_confidence, primary_distance, secondary_distance,
                face_quality, method, captured_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            insert_rows,
        )
    return len(insert_rows)


def migrate(sqlite_path: str, postgres_url: str) -> None:
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg.connect(_normalize_postgres_url(postgres_url))

    migrated_tables = []
    for table in TABLES:
        if not _table_exists_sqlite(sqlite_conn, table):
            print(f"Skip (sqlite missing): {table}")
            continue
        if not _table_exists_postgres(pg_conn, table):
            print(f"Skip (postgres missing): {table}")
            continue

        cols, rows = _fetch_sqlite_rows(sqlite_conn, table)
        with pg_conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
        _copy_rows_postgres(pg_conn, table, cols, rows)
        _reset_postgres_sequences(pg_conn, table)
        migrated_tables.append((table, len(rows)))
        print(f"Migrated {table}: {len(rows)} rows")

    backfilled = _backfill_user_embeddings_from_users(pg_conn)
    if backfilled > 0:
        print(f"Backfilled user_embeddings: {backfilled} embedding rows")
    backfilled_events = _backfill_legacy_recognition_log_into_events(sqlite_conn, pg_conn)
    if backfilled_events > 0:
        print(
            "Backfilled recognition_events from legacy SQLite recognition_log: "
            f"{backfilled_events} event rows"
        )

    pg_conn.commit()

    print("\nValidation summary:")
    for table, sqlite_count in migrated_tables:
        with pg_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = int(cur.fetchone()[0])
        status = "OK" if pg_count == sqlite_count else "MISMATCH"
        print(f"- {table}: sqlite={sqlite_count}, postgres={pg_count} [{status}]")

    sqlite_conn.close()
    pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="One-time SQLite -> PostgreSQL migrator.")
    parser.add_argument("--sqlite-path", required=True, help="Path to SQLite database file.")
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL"),
        help="Target PostgreSQL DATABASE_URL (defaults to env DATABASE_URL).",
    )
    args = parser.parse_args()
    postgres_url = (args.postgres_url or "").strip()
    if not postgres_url:
        parser.error("--postgres-url is required (or set DATABASE_URL in environment).")
    migrate(args.sqlite_path, postgres_url)


if __name__ == "__main__":
    main()
