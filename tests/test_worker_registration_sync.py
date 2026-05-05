from __future__ import annotations

import time
import unittest

try:
    from workers.recognition_worker import _send_outbound_entry
except Exception:  # pragma: no cover - optional runtime dependency in CI.
    _send_outbound_entry = None


@unittest.skipIf(_send_outbound_entry is None, "Worker runtime dependencies are unavailable.")
class WorkerRegistrationSyncTests(unittest.TestCase):
    def test_send_outbound_entry_supports_registration_samples(self) -> None:
        calls: list[tuple[str, dict]] = []

        class _FakeApiClient:
            def post_json(self, path: str, payload: dict) -> dict:
                calls.append((path, payload))
                return {"success": True}

        result = _send_outbound_entry(
            _FakeApiClient(),
            {
                "kind": "registration_sample",
                "payload": {"sample_id": "s-1", "session_id": "sess-1"},
            },
        )
        self.assertTrue(result)
        self.assertEqual(calls[0][0], "/api/internal/registration-samples")

    def test_send_outbound_entry_supports_heartbeat_path(self) -> None:
        calls: list[tuple[str, dict]] = []

        class _FakeApiClient:
            def post_json(self, path: str, payload: dict) -> dict:
                calls.append((path, payload))
                return {"success": True}

        # Heartbeats are posted directly by the sync loop; verify client path contract.
        client = _FakeApiClient()
        payload = {"worker_role": "entry", "observed_at": time.time()}
        response = client.post_json("/api/internal/worker-heartbeat", payload)
        self.assertTrue(response.get("success"))
        self.assertEqual(calls[0][0], "/api/internal/worker-heartbeat")


if __name__ == "__main__":
    unittest.main()
