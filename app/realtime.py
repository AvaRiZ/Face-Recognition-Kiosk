from __future__ import annotations

from flask_socketio import SocketIO


socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="threading",
)


def emit_analytics_update(reason: str, payload: dict | None = None) -> None:
    event_payload = {"reason": reason}
    if payload:
        event_payload.update(payload)
    socketio.emit("analytics_updated", event_payload)
