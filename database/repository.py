"""
User and Recognition Event Repository

DATABASE SCHEMA POLICY (as of Alembic 0003):
    - recognition_events: CANONICAL source of truth for face recognition events
      * Contains: event_id (unique), decision, confidence scores, full payload, timestamps
      * Records persist even if user is deleted (ON DELETE SET NULL)
      * Use for: new event ingestion, auditing, future analytics
    
    - recognition_log: LEGACY compatibility layer (maintained for backwards compatibility)
      * Contains: subset of recognition_events data (user_id, confidence, method, timestamps)
      * Records remain if user is deleted (ON DELETE SET NULL as of Alembic 0003)
      * Use for: existing dashboards/analytics only (planned deprecation)
    
    - user_embeddings: Per-user ML model embeddings
      * Uses CASCADE delete: embeddings are purged when user is deleted
      * Timestamps standardized to TIMESTAMPTZ (as of Alembic 0003)
    
    - users: Core identity/profile table
      * Archival/deletion now works correctly (no FK blocks on recognition_log)
      * See: docs/database_schema_policy.md for complete migration details
"""

from __future__ import annotations

import json
import pickle
import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.realtime import emit_analytics_update
from core.models import RecognitionResult, User
from core.program_catalog import (
    OTHER_COLLEGE_LABEL,
    iter_program_catalog_records,
    program_code_for,
)
from db import connect as db_connect
from db import table_columns
from services.embedding_service import (
    count_embeddings,
    infer_embedding_dim,
    merge_embeddings_by_model,
    normalize_embeddings_by_model,
)


class UserRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def init_db(self) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        dialect = getattr(conn, "dialect", "sqlite")

        if dialect == "postgres":
            required_tables = (
                "users",
                "recognition_log",
                "programs",
                "recognition_events",
                "user_embeddings",
            )
            missing = [name for name in required_tables if not table_columns(conn, name)]
            if missing:
                conn.close()
                raise RuntimeError(
                    "PostgreSQL schema is missing required repository tables "
                    f"{missing}. Run `alembic upgrade head` before starting the app."
                )
            _seed_programs_table(c)
            conn.commit()
            conn.close()
            return

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                sr_code TEXT UNIQUE,
                gender TEXT,
                course TEXT,
                embeddings BLOB NOT NULL,
                image_paths TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                archived_at TIMESTAMP
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS recognition_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                confidence REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                primary_confidence REAL,
                secondary_confidence REAL,
                primary_distance REAL,
                secondary_distance REAL,
                face_quality REAL,
                method TEXT DEFAULT 'two-factor',
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS programs (
                program_id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_name TEXT NOT NULL UNIQUE,
                program_code TEXT,
                department_name TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        existing_columns = table_columns(conn, "recognition_log")
        extra_columns = {
            "primary_confidence": "REAL",
            "secondary_confidence": "REAL",
            "primary_distance": "REAL",
            "secondary_distance": "REAL",
            "face_quality": "REAL",
            "method": "TEXT DEFAULT 'two-factor'",
        }
        for col_name, col_type in extra_columns.items():
            if col_name not in existing_columns:
                c.execute(f"ALTER TABLE recognition_log ADD COLUMN {col_name} {col_type}")

        existing_columns = table_columns(conn, "users")
        if "gender" not in existing_columns:
            c.execute("ALTER TABLE users ADD COLUMN gender TEXT")
        if "archived_at" not in existing_columns:
            c.execute("ALTER TABLE users ADD COLUMN archived_at TIMESTAMP")

        existing_program_columns = table_columns(conn, "programs")
        if "program_code" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN program_code TEXT")
        if "department_name" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN department_name TEXT")
        if "is_active" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN is_active INTEGER DEFAULT 1")
        if "created_at" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        if "last_updated" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_recognition_log_user_id
            ON recognition_log(user_id)
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_recognition_log_timestamp
            ON recognition_log(timestamp)
            """
        )

        _seed_programs_table(c)

        conn.commit()
        conn.close()

    def get_all_users(self) -> list[User]:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course, embeddings, image_paths, embedding_dim
            FROM users
            WHERE archived_at IS NULL
            """
        )
        rows = c.fetchall()

        user_ids = [int(row[0]) for row in rows]
        embeddings_by_user_id = self._load_embeddings_for_users(c, user_ids)
        conn.close()

        users: list[User] = []
        for user_id, name, sr_code, gender, program, legacy_emb_blob, image_paths_raw, embedding_dim in rows:
            embeddings = embeddings_by_user_id.get(int(user_id)) or self._deserialize_legacy_embeddings_blob(legacy_emb_blob)
            image_paths = image_paths_raw.split(";") if image_paths_raw else []
            users.append(
                User(
                    id=user_id,
                    name=name or "",
                    sr_code=sr_code or "",
                    gender=gender or "",
                    program=program or "",
                    embeddings=embeddings,
                    image_paths=image_paths,
                    embedding_dim=int(embedding_dim or infer_embedding_dim(embeddings)),
                )
            )
        return users

    def get_user_by_sr_code(self, sr_code: str) -> Optional[User]:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course, embeddings, image_paths, embedding_dim
            FROM users
            WHERE sr_code = ?
            """,
            (sr_code,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return None

        embeddings = self._load_embeddings_for_users(c, [int(row[0])]).get(int(row[0])) or self._deserialize_legacy_embeddings_blob(row[5])
        conn.close()

        image_paths = row[6].split(";") if row[6] else []
        return User(
            id=row[0],
            name=row[1] or "",
            sr_code=row[2] or "",
            gender=row[3] or "",
            program=row[4] or "",
            embeddings=embeddings,
            image_paths=image_paths,
            embedding_dim=int(row[7] or infer_embedding_dim(embeddings)),
        )

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course, embeddings, image_paths, embedding_dim
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return None

        embeddings = self._load_embeddings_for_users(c, [int(row[0])]).get(int(row[0])) or self._deserialize_legacy_embeddings_blob(row[5])
        conn.close()

        image_paths = row[6].split(";") if row[6] else []
        return User(
            id=row[0],
            name=row[1] or "",
            sr_code=row[2] or "",
            gender=row[3] or "",
            program=row[4] or "",
            embeddings=embeddings,
            image_paths=image_paths,
            embedding_dim=int(row[7] or infer_embedding_dim(embeddings)),
        )

    def save_user(self, user: User) -> int:
        conn = db_connect(self.db_path)
        c = conn.cursor()

        normalized_embeddings = normalize_embeddings_by_model(user.embeddings)
        supports_embedding_table = self._supports_embedding_table(conn)

        c.execute("SELECT user_id, embeddings, image_paths FROM users WHERE sr_code = ?", (user.sr_code,))
        existing = c.fetchone()
        if existing:
            user_id = int(existing[0])
            existing_embeddings = self._load_embeddings_for_users(c, [user_id]).get(user_id)
            if not existing_embeddings:
                existing_embeddings = self._deserialize_legacy_embeddings_blob(existing[1])
            merged_embeddings = merge_embeddings_by_model(existing_embeddings, normalized_embeddings)
            merged_paths = (existing[2].split(";") if existing[2] else []) + list(user.image_paths)

            if supports_embedding_table:
                c.execute(
                    """
                    UPDATE users
                    SET name = ?,
                        gender = ?,
                        course = ?,
                        image_paths = ?,
                        embedding_dim = ?,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (
                        user.name,
                        user.gender,
                        user.program,
                        ";".join(merged_paths),
                        infer_embedding_dim(merged_embeddings),
                        user_id,
                    ),
                )
            else:
                c.execute(
                    """
                    UPDATE users
                    SET name = ?,
                        gender = ?,
                        course = ?,
                        embeddings = ?,
                        image_paths = ?,
                        embedding_dim = ?,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (
                        user.name,
                        user.gender,
                        user.program,
                        self._serialize_legacy_embeddings_blob(merged_embeddings),
                        ";".join(merged_paths),
                        infer_embedding_dim(merged_embeddings),
                        user_id,
                    ),
                )
        else:
            merged_embeddings = normalized_embeddings
            if supports_embedding_table:
                legacy_blob = b""
            else:
                legacy_blob = self._serialize_legacy_embeddings_blob(normalized_embeddings)

            if getattr(conn, "dialect", "sqlite") == "postgres":
                c.execute(
                    """
                    INSERT INTO users (name, sr_code, gender, course, embeddings, image_paths, embedding_dim)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    RETURNING user_id
                    """,
                    (
                        user.name,
                        user.sr_code,
                        user.gender,
                        user.program,
                        legacy_blob,
                        ";".join(user.image_paths),
                        infer_embedding_dim(normalized_embeddings),
                    ),
                )
                user_id = int(c.fetchone()[0])
            else:
                c.execute(
                    """
                    INSERT INTO users (name, sr_code, gender, course, embeddings, image_paths, embedding_dim)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.name,
                        user.sr_code,
                        user.gender,
                        user.program,
                        legacy_blob,
                        ";".join(user.image_paths),
                        infer_embedding_dim(normalized_embeddings),
                    ),
                )
                user_id = int(c.lastrowid)

        if supports_embedding_table:
            self._replace_user_embeddings(c, int(user_id), merged_embeddings)

        _upsert_program_record(c, user.program)

        conn.commit()
        conn.close()
        emit_analytics_update("user_saved", {"user_id": int(user_id)})
        return int(user_id)

    def update_embeddings(self, user_id: int, new_embeddings: dict[str, list[np.ndarray]], image_path: str | None = None) -> User | None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT name, sr_code, gender, course, embeddings, image_paths, embedding_dim
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return None

        name, sr_code, gender, program, existing_emb_blob, existing_paths_str, _ = row
        supports_embedding_table = self._supports_embedding_table(conn)

        existing_embeddings = self._load_embeddings_for_users(c, [int(user_id)]).get(int(user_id))
        if not existing_embeddings:
            existing_embeddings = self._deserialize_legacy_embeddings_blob(existing_emb_blob)

        merged_embeddings = merge_embeddings_by_model(existing_embeddings, new_embeddings)
        image_paths = existing_paths_str.split(";") if existing_paths_str else []
        if image_path:
            image_paths.append(image_path)

        if supports_embedding_table:
            c.execute(
                """
                UPDATE users
                SET image_paths = ?, embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    ";".join(image_paths),
                    infer_embedding_dim(merged_embeddings),
                    user_id,
                ),
            )
            self._replace_user_embeddings(c, int(user_id), merged_embeddings)
        else:
            c.execute(
                """
                UPDATE users
                SET embeddings = ?, image_paths = ?, embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    self._serialize_legacy_embeddings_blob(merged_embeddings),
                    ";".join(image_paths),
                    infer_embedding_dim(merged_embeddings),
                    user_id,
                ),
            )

        conn.commit()
        conn.close()
        emit_analytics_update("user_embeddings_updated", {"user_id": int(user_id)})

        return User(
            id=user_id,
            name=name or "",
            sr_code=sr_code or "",
            gender=gender or "",
            program=program or "",
            embeddings=merged_embeddings,
            image_paths=image_paths,
            embedding_dim=infer_embedding_dim(merged_embeddings),
        )

    def delete_user(self, user_id: int) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        if self._supports_embedding_table(conn):
            c.execute("DELETE FROM user_embeddings WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM recognition_log WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        emit_analytics_update("user_deleted", {"user_id": int(user_id)})

    def reset_database(self) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        for table_name in ("recognition_events", "user_embeddings", "recognition_log", "users"):
            if table_columns(conn, table_name):
                c.execute(f"DELETE FROM {table_name}")
        conn.commit()
        conn.close()
        emit_analytics_update("database_reset")

    def log_recognition(
        self,
        result: RecognitionResult,
        face_quality: float | None = None,
        method: str = "two-factor",
    ) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()

        confidence = _coerce_float(result.confidence) or 0.0
        primary_confidence = _coerce_float(result.primary_confidence)
        secondary_confidence = _coerce_float(result.secondary_confidence)
        primary_distance = _coerce_float(result.primary_distance)
        secondary_distance = _coerce_float(result.secondary_distance)
        quality_value = _coerce_float(face_quality)
        captured_at = datetime.now(timezone.utc)

        inserted_event = False
        if table_columns(conn, "recognition_events"):
            event_id = f"evt-{uuid.uuid4().hex}"
            payload_json = json.dumps(
                {
                    "event_id": event_id,
                    "station_id": "entrance-station-1",
                    "user_id": int(result.user_id),
                    "sr_code": result.user.sr_code,
                    "decision": "allowed",
                    "confidence": confidence,
                    "primary_confidence": primary_confidence,
                    "secondary_confidence": secondary_confidence,
                    "primary_distance": primary_distance,
                    "secondary_distance": secondary_distance,
                    "face_quality": quality_value,
                    "method": method,
                    "captured_at": captured_at.isoformat(),
                },
                ensure_ascii=True,
            )
            c.execute(
                """
                INSERT INTO recognition_events (
                    event_id, station_id, user_id, sr_code, decision, confidence,
                    primary_confidence, secondary_confidence, primary_distance, secondary_distance,
                    face_quality, method, captured_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event_id,
                    "entrance-station-1",
                    int(result.user_id),
                    result.user.sr_code,
                    "allowed",
                    confidence,
                    primary_confidence,
                    secondary_confidence,
                    primary_distance,
                    secondary_distance,
                    quality_value,
                    method,
                    captured_at,
                    payload_json,
                ),
            )
            inserted_event = (c.rowcount or 0) > 0

        if table_columns(conn, "recognition_log"):
            c.execute(
                """
                INSERT INTO recognition_log (
                    user_id, confidence, primary_confidence, secondary_confidence,
                    primary_distance, secondary_distance, face_quality, method, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(result.user_id),
                    confidence,
                    primary_confidence,
                    secondary_confidence,
                    primary_distance,
                    secondary_distance,
                    quality_value,
                    method,
                    captured_at,
                ),
            )

        conn.commit()
        conn.close()

        emit_analytics_update("recognition_logged", {"user_id": int(result.user_id)})
        if inserted_event:
            emit_analytics_update(
                "recognition_event_ingested",
                {"user_id": int(result.user_id)},
            )

    def get_recognition_statistics(self):
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT u.user_id, u.name, u.sr_code, u.embedding_dim, u.embeddings,
                   COUNT(r.log_id) as recognitions,
                   AVG(r.confidence) as avg_confidence,
                   MAX(r.confidence) as best_confidence,
                   MAX(r.timestamp) as last_seen
            FROM users u
            LEFT JOIN recognition_log r ON u.user_id = r.user_id
            GROUP BY u.user_id
            ORDER BY recognitions DESC
            """
        )
        rows = c.fetchall()
        conn.close()
        return rows

    def get_latest_recognition_detail(self, user_id: int):
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT timestamp, method, primary_confidence, secondary_confidence,
                   primary_distance, secondary_distance, face_quality
            FROM recognition_log
            WHERE user_id = ?
            ORDER BY timestamp DESC, log_id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = c.fetchone()
        conn.close()
        return row

    def count_user_embeddings(self, user_id: int) -> int:
        conn = db_connect(self.db_path)
        c = conn.cursor()

        if self._supports_embedding_table(conn):
            c.execute("SELECT COUNT(*) FROM user_embeddings WHERE user_id = ?", (user_id,))
            count = int(c.fetchone()[0] or 0)
            conn.close()
            return count

        c.execute("SELECT embeddings FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        if not row or not row[0]:
            return 0
        embeddings = self._deserialize_legacy_embeddings_blob(row[0])
        return count_embeddings(embeddings)

    def _supports_embedding_table(self, conn) -> bool:
        return bool(table_columns(conn, "user_embeddings"))

    def _load_embeddings_for_users(self, cursor, user_ids: list[int]) -> dict[int, dict[str, list[np.ndarray]]]:
        if not user_ids:
            return {}

        placeholders = ",".join("?" for _ in user_ids)
        try:
            cursor.execute(
                f"""
                SELECT user_id, model_name, embedding
                FROM user_embeddings
                WHERE user_id IN ({placeholders})
                ORDER BY user_id ASC, embedding_id ASC
                """,
                tuple(int(user_id) for user_id in user_ids),
            )
        except Exception:
            return {}

        out: dict[int, dict[str, list[np.ndarray]]] = {}
        for user_id, model_name, emb_blob in cursor.fetchall():
            emb = self._decode_embedding_blob(emb_blob)
            if emb is None:
                continue
            uid = int(user_id)
            model_key = str(model_name or "")
            if not model_key:
                continue
            out.setdefault(uid, {}).setdefault(model_key, []).append(emb)
        return out

    def _replace_user_embeddings(self, cursor, user_id: int, embeddings_by_model: dict[str, list[np.ndarray]]) -> None:
        cursor.execute("DELETE FROM user_embeddings WHERE user_id = ?", (user_id,))

        normalized = normalize_embeddings_by_model(embeddings_by_model)
        rows = []
        for model_name, vectors in normalized.items():
            for vector in vectors:
                blob = self._encode_embedding_blob(vector)
                if blob is None:
                    continue
                rows.append((int(user_id), str(model_name), blob))

        if rows:
            cursor.executemany(
                """
                INSERT INTO user_embeddings (user_id, model_name, embedding)
                VALUES (?, ?, ?)
                """,
                rows,
            )

    def _encode_embedding_blob(self, vector) -> bytes | None:
        try:
            arr = np.asarray(vector, dtype=np.float32)
        except Exception:
            return None
        if arr.ndim != 1 or arr.size == 0:
            return None
        return arr.tobytes()

    def _decode_embedding_blob(self, value) -> np.ndarray | None:
        if value is None:
            return None
        if isinstance(value, memoryview):
            raw = value.tobytes()
        elif isinstance(value, bytearray):
            raw = bytes(value)
        elif isinstance(value, bytes):
            raw = value
        else:
            return None
        if not raw or (len(raw) % 4) != 0:
            return None
        try:
            return np.frombuffer(raw, dtype=np.float32).astype(np.float32, copy=True)
        except Exception:
            return None

    def _deserialize_legacy_embeddings_blob(self, value) -> dict[str, list[np.ndarray]]:
        if value is None:
            return {}
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, bytearray):
            value = bytes(value)
        if not isinstance(value, bytes) or not value:
            return {}

        try:
            payload = pickle.loads(value)
        except Exception:
            return {}

        if isinstance(payload, dict):
            return normalize_embeddings_by_model(payload)

        if isinstance(payload, np.ndarray):
            payload = [payload]

        if isinstance(payload, (list, tuple)):
            legacy_vectors = []
            for item in payload:
                try:
                    arr = np.asarray(item, dtype=np.float32)
                except Exception:
                    continue
                if arr.ndim == 1 and arr.size > 0:
                    legacy_vectors.append(arr)
                elif arr.ndim == 2:
                    for row in arr:
                        if row.size > 0:
                            legacy_vectors.append(np.asarray(row, dtype=np.float32))
            if legacy_vectors:
                return {"legacy": legacy_vectors}

        return {}

    def _serialize_legacy_embeddings_blob(self, embeddings_by_model: dict[str, list[np.ndarray]]) -> bytes:
        normalized = normalize_embeddings_by_model(embeddings_by_model)
        return pickle.dumps(normalized)


def _coerce_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes):
        if not value:
            return None
        try:
            return float(value.decode("utf-8").strip())
        except Exception:
            pass
        if len(value) == 8:
            try:
                return float(np.frombuffer(value, dtype=np.float64, count=1)[0])
            except Exception:
                pass
        if len(value) == 4:
            try:
                return float(np.frombuffer(value, dtype=np.float32, count=1)[0])
            except Exception:
                pass
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_program_name(program_name: str | None) -> str:
    return " ".join((program_name or "").split())


def _upsert_program_record(
    cursor,
    program_name: str | None,
    department_name: str | None = None,
    program_code: str | None = None,
) -> None:
    normalized_program = _normalize_program_name(program_name)
    if not normalized_program:
        return

    normalized_department = " ".join((department_name or "").split()) or OTHER_COLLEGE_LABEL
    normalized_code = _normalize_program_name(program_code) or program_code_for(normalized_program)
    cursor.execute(
        """
        INSERT INTO programs (program_name, program_code, department_name, is_active)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(program_name) DO UPDATE SET
            program_code = CASE
                WHEN (programs.program_code IS NULL OR TRIM(programs.program_code) = '')
                     AND excluded.program_code IS NOT NULL
                     AND TRIM(excluded.program_code) <> ''
                    THEN excluded.program_code
                ELSE programs.program_code
            END,
            department_name = CASE
                WHEN programs.department_name IS NULL OR TRIM(programs.department_name) = '' OR programs.department_name = ?
                    THEN excluded.department_name
                ELSE programs.department_name
            END,
            is_active = 1,
            last_updated = CURRENT_TIMESTAMP
        """,
        (normalized_program, normalized_code, normalized_department, OTHER_COLLEGE_LABEL),
    )


def _seed_programs_table(cursor) -> None:
    for department_name, program_name, program_code in iter_program_catalog_records():
        _upsert_program_record(cursor, program_name, department_name, program_code)

    cursor.execute(
        """
        SELECT DISTINCT COALESCE(NULLIF(TRIM(course), ''), '')
        FROM users
        """
    )
    for (program_name,) in cursor.fetchall():
        _upsert_program_record(cursor, program_name)
