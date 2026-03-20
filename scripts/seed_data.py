import argparse
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.face_service import save_user_with_multiple_embeddings


def _random_name():
    first = [
        "Ava", "Liam", "Noah", "Mia", "Ethan", "Zoe", "Aria", "Levi",
        "Ivy", "Maya", "Lucas", "Nora", "Kai", "Luna", "Ella", "Owen",
    ]
    last = [
        "Reyes", "Santos", "Garcia", "Lopez", "Lee", "Ramos", "Cruz",
        "Lim", "Gomez", "Dela Cruz", "Torres", "Diaz", "Tan", "Co",
    ]
    return f"{random.choice(first)} {random.choice(last)}"


def _random_sr_code(idx):
    return f"SR{2026}{idx:04d}"


def _random_course():
    return random.choice(
        ["BSCS", "BSIT", "BSIS", "BSEMC", "BSCE", "BSA", "BSBA", "BSED"]
    )


def _random_embeddings(dim, count=3):
    embeddings = []
    for _ in range(count):
        vec = np.random.rand(dim).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        embeddings.append(vec)
    return embeddings


def _insert_logs(db_path, user_ids, days, avg_logs_per_day):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    start_date = datetime.now() - timedelta(days=days - 1)
    for day_offset in range(days):
        day = start_date + timedelta(days=day_offset)
        logs_today = max(1, int(random.gauss(avg_logs_per_day, avg_logs_per_day * 0.25)))
        for _ in range(logs_today):
            user_id = random.choice(user_ids)
            confidence = round(random.uniform(0.72, 0.99), 4)
            timestamp = day + timedelta(
                hours=random.randint(7, 20),
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59),
            )
            c.execute(
                "INSERT INTO recognition_log (user_id, confidence, timestamp) VALUES (?, ?, ?)",
                (user_id, confidence, timestamp.strftime("%Y-%m-%d %H:%M:%S")),
            )

    conn.commit()
    conn.close()


def _reset_db(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM recognition_log")
    c.execute("DELETE FROM users")
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Seed database with sample UI data.")
    parser.add_argument("--db", default="database/faces_improved.db")
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--avg-logs", type=int, default=40)
    parser.add_argument("--embedding-dim", type=int, default=128)
    parser.add_argument("--reset", action="store_true", help="Clear existing data before seeding")
    args = parser.parse_args()

    if args.reset:
        _reset_db(args.db)

    user_ids = []
    for i in range(1, args.users + 1):
        name = _random_name()
        sr_code = _random_sr_code(i)
        course = _random_course()
        embeddings = _random_embeddings(args.embedding_dim, count=3)
        image_paths = [f"faces_improved/{sr_code}/face_seed_{j}.jpg" for j in range(1, 4)]
        user_id = save_user_with_multiple_embeddings(
            args.db, embeddings, image_paths, name, sr_code, course
        )
        user_ids.append(user_id)

    _insert_logs(args.db, user_ids, args.days, args.avg_logs)
    print(f"Seeded {len(user_ids)} users and ~{args.days * args.avg_logs} logs.")


if __name__ == "__main__":
    main()
