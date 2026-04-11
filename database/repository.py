from __future__ import annotations

import pickle
from typing import Optional

import numpy as np

from core.models import RecognitionResult, User
from core.program_catalog import OTHER_COLLEGE_LABEL, iter_program_catalog
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
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS programs (
                program_id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_name TEXT NOT NULL UNIQUE,
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
        if "department_name" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN department_name TEXT")
        if "is_active" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN is_active INTEGER DEFAULT 1")
        if "created_at" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        if "last_updated" not in existing_program_columns:
            c.execute("ALTER TABLE programs ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

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
        conn.close()

        users: list[User] = []
        for user_id, name, sr_code, gender, program, emb_blob, image_paths_raw, embedding_dim in rows:
            embeddings = {}
            if emb_blob:
                embeddings = normalize_embeddings_by_model(pickle.loads(emb_blob))

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
        conn.close()
        if not row:
            return None

        user_id, name, sr_code, gender, program, emb_blob, image_paths_raw, embedding_dim = row
        embeddings = normalize_embeddings_by_model(pickle.loads(emb_blob)) if emb_blob else {}
        image_paths = image_paths_raw.split(";") if image_paths_raw else []
        return User(
            id=user_id,
            name=name or "",
            sr_code=sr_code or "",
            gender=gender or "",
            program=program or "",
            embeddings=embeddings,
            image_paths=image_paths,
            embedding_dim=int(embedding_dim or infer_embedding_dim(embeddings)),
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
        conn.close()
        if not row:
            return None

        user_id, name, sr_code, gender, program, emb_blob, image_paths_raw, embedding_dim = row
        embeddings = normalize_embeddings_by_model(pickle.loads(emb_blob)) if emb_blob else {}
        image_paths = image_paths_raw.split(";") if image_paths_raw else []
        return User(
            id=user_id,
            name=name or "",
            sr_code=sr_code or "",
            gender=gender or "",
            program=program or "",
            embeddings=embeddings,
            image_paths=image_paths,
            embedding_dim=int(embedding_dim or infer_embedding_dim(embeddings)),
        )

    def save_user(self, user: User) -> int:
        conn = db_connect(self.db_path)
        c = conn.cursor()

        normalized_embeddings = normalize_embeddings_by_model(user.embeddings)

        c.execute("SELECT user_id, embeddings, image_paths FROM users WHERE sr_code = ?", (user.sr_code,))
        existing = c.fetchone()
        if existing:
            user_id, existing_emb_blob, existing_paths_str = existing
            existing_embeddings = normalize_embeddings_by_model(pickle.loads(existing_emb_blob)) if existing_emb_blob else {}
            merged_embeddings = merge_embeddings_by_model(existing_embeddings, normalized_embeddings)
            merged_paths = (existing_paths_str.split(";") if existing_paths_str else []) + list(user.image_paths)
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
                    pickle.dumps(merged_embeddings),
                    ";".join(merged_paths),
                    infer_embedding_dim(merged_embeddings),
                    user_id,
                ),
            )
        else:
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
                        pickle.dumps(normalized_embeddings),
                        ";".join(user.image_paths),
                        infer_embedding_dim(normalized_embeddings),
                    ),
                )
                user_id = c.fetchone()[0]
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
                        pickle.dumps(normalized_embeddings),
                        ";".join(user.image_paths),
                        infer_embedding_dim(normalized_embeddings),
                    ),
                )
                user_id = c.lastrowid

        _upsert_program_record(c, user.program)

        conn.commit()
        conn.close()
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
        existing_embeddings = normalize_embeddings_by_model(pickle.loads(existing_emb_blob)) if existing_emb_blob else {}
        merged_embeddings = merge_embeddings_by_model(existing_embeddings, new_embeddings)
        image_paths = existing_paths_str.split(";") if existing_paths_str else []
        if image_path:
            image_paths.append(image_path)

        c.execute(
            """
            UPDATE users
            SET embeddings = ?, image_paths = ?, embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (
                pickle.dumps(merged_embeddings),
                ";".join(image_paths),
                infer_embedding_dim(merged_embeddings),
                user_id,
            ),
        )
        conn.commit()
        conn.close()

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
        c.execute("DELETE FROM recognition_log WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def reset_database(self) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM recognition_log")
        conn.commit()
        conn.close()

    def log_recognition(
        self,
        result: RecognitionResult,
        face_quality: float | None = None,
        method: str = "two-factor",
    ) -> None:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO recognition_log (
                user_id, confidence, primary_confidence, secondary_confidence,
                primary_distance, secondary_distance, face_quality, method
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.user_id,
                _coerce_float(result.confidence) or 0.0,
                _coerce_float(result.primary_confidence),
                _coerce_float(result.secondary_confidence),
                _coerce_float(result.primary_distance),
                _coerce_float(result.secondary_distance),
                _coerce_float(face_quality),
                method,
            ),
        )
        conn.commit()
        conn.close()

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
        c.execute("SELECT embeddings FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        if not row or not row[0]:
            return 0
        embeddings = normalize_embeddings_by_model(pickle.loads(row[0]))
        return count_embeddings(embeddings)


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


def _upsert_program_record(cursor, program_name: str | None, department_name: str | None = None) -> None:
    normalized_program = _normalize_program_name(program_name)
    if not normalized_program:
        return

    normalized_department = " ".join((department_name or "").split()) or OTHER_COLLEGE_LABEL
    cursor.execute(
        """
        INSERT INTO programs (program_name, department_name, is_active)
        VALUES (?, ?, 1)
        ON CONFLICT(program_name) DO UPDATE SET
            department_name = CASE
                WHEN programs.department_name IS NULL OR TRIM(programs.department_name) = '' OR programs.department_name = ?
                    THEN excluded.department_name
                ELSE programs.department_name
            END,
            is_active = 1,
            last_updated = CURRENT_TIMESTAMP
        """,
        (normalized_program, normalized_department, OTHER_COLLEGE_LABEL),
    )


def _seed_programs_table(cursor) -> None:
    for department_name, program_name in iter_program_catalog():
        _upsert_program_record(cursor, program_name, department_name)

    cursor.execute(
        """
        SELECT DISTINCT COALESCE(NULLIF(TRIM(course), ''), '')
        FROM users
        """
    )
    for (program_name,) in cursor.fetchall():
        _upsert_program_record(cursor, program_name)
