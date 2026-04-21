from __future__ import annotations

import json
import os
import time
import uuid
from typing import Callable


class DurableOutboundQueue:
    def __init__(self, queue_dir: str, base_backoff_seconds: float = 1.0, max_backoff_seconds: float = 60.0):
        self.queue_dir = queue_dir
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        os.makedirs(self.queue_dir, exist_ok=True)

    def enqueue(self, kind: str, payload: dict) -> str:
        entry_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        entry_path = os.path.join(self.queue_dir, f"{entry_id}.json")
        entry = {
            "id": entry_id,
            "kind": str(kind),
            "payload": payload,
            "attempts": 0,
            "next_attempt_at": 0.0,
            "last_error": "",
            "created_at": time.time(),
        }
        with open(entry_path, "w", encoding="utf-8") as fp:
            json.dump(entry, fp, ensure_ascii=True)
        return entry_id

    def _iter_entry_paths(self) -> list[str]:
        files = [name for name in os.listdir(self.queue_dir) if name.endswith(".json")]
        files.sort()
        return [os.path.join(self.queue_dir, name) for name in files]

    def drain_once(self, sender: Callable[[dict], bool]) -> tuple[int, int]:
        sent = 0
        remaining = 0
        now = time.time()
        for entry_path in self._iter_entry_paths():
            try:
                with open(entry_path, "r", encoding="utf-8") as fp:
                    entry = json.load(fp)
            except Exception:
                remaining += 1
                continue

            if float(entry.get("next_attempt_at", 0.0) or 0.0) > now:
                remaining += 1
                continue

            try:
                ok = bool(sender(entry))
            except Exception as exc:
                ok = False
                entry["last_error"] = str(exc)

            if ok:
                try:
                    os.remove(entry_path)
                except FileNotFoundError:
                    pass
                sent += 1
                continue

            attempts = int(entry.get("attempts", 0) or 0) + 1
            backoff = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** max(0, attempts - 1)))
            entry["attempts"] = attempts
            entry["next_attempt_at"] = time.time() + backoff

            with open(entry_path, "w", encoding="utf-8") as fp:
                json.dump(entry, fp, ensure_ascii=True)
            remaining += 1
        return sent, remaining

