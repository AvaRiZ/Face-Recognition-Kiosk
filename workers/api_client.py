from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout_seconds: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.token = (token or "").strip()
        self.timeout_seconds = float(timeout_seconds)

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8", errors="ignore")
                parsed = json.loads(raw) if raw else None
                return status, parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(raw) if raw else None
            except Exception:
                parsed = {"message": raw}
            return int(exc.code), parsed

    def get_json(self, path: str) -> dict:
        status, payload = self._request("GET", path)
        if status < 200 or status >= 300:
            message = payload.get("message") if isinstance(payload, dict) else str(payload)
            raise RuntimeError(f"GET {path} failed ({status}): {message}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"GET {path} returned invalid payload.")
        return payload

    def post_json(self, path: str, payload: dict) -> dict:
        status, body = self._request("POST", path, payload=payload)
        if status < 200 or status >= 300:
            message = body.get("message") if isinstance(body, dict) else str(body)
            raise RuntimeError(f"POST {path} failed ({status}): {message}")
        if not isinstance(body, dict):
            raise RuntimeError(f"POST {path} returned invalid payload.")
        return body

