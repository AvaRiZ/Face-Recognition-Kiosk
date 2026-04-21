from __future__ import annotations

import os
import tempfile
import unittest

from workers.durable_queue import DurableOutboundQueue


class DurableQueueTests(unittest.TestCase):
    def test_queue_drains_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = DurableOutboundQueue(queue_dir=temp_dir, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
            queue.enqueue("recognition_event", {"event_id": "evt-1"})

            sent, remaining = queue.drain_once(lambda _entry: True)
            self.assertEqual(sent, 1)
            self.assertEqual(remaining, 0)
            self.assertEqual(os.listdir(temp_dir), [])

    def test_queue_retries_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = DurableOutboundQueue(queue_dir=temp_dir, base_backoff_seconds=0.01, max_backoff_seconds=0.01)
            queue.enqueue("recognition_event", {"event_id": "evt-2"})

            sent, remaining = queue.drain_once(lambda _entry: False)
            self.assertEqual(sent, 0)
            self.assertEqual(remaining, 1)
            self.assertEqual(len(os.listdir(temp_dir)), 1)


if __name__ == "__main__":
    unittest.main()
