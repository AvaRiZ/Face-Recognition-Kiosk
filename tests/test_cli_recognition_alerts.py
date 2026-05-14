import unittest
import sys
import types
from unittest.mock import patch

realtime_stub = sys.modules.setdefault("app.realtime", types.ModuleType("app.realtime"))
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
realtime_stub.emit_capacity_threshold_alert = lambda *args, **kwargs: None
realtime_stub.emit_unrecognized_detection = lambda *args, **kwargs: None

from app.cli import CLIApplication
from core.config import AppConfig
from core.models import TrackingState
from core.state import AppStateManager
from services.tracking_service import TrackingService


class _FakeConfig:
    pass


class _FakeRepository:
    def __init__(self):
        self.logged = []
        self.revoked = []

    def log_unrecognized_detection(self, **kwargs):
        self.logged.append(kwargs)
        return True

    def revoke_unrecognized_detection(self, **kwargs):
        self.revoked.append(kwargs)
        return True


class _FakeState:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_track_state(self, track_id):
        return self.tracks.get(track_id)


def _cli_for_role(worker_role: str) -> CLIApplication:
    cli = CLIApplication.__new__(CLIApplication)
    cli.worker_role = worker_role
    cli.config = _FakeConfig()
    cli._greeting_popup_name = ""
    cli._greeting_popup_active_until = 0.0
    cli._greeting_popup_duration_seconds = 4.0
    cli._greeting_same_user_cooldown_seconds = 8.0
    cli._greeting_last_shown_by_user = {}
    return cli


class CliRecognitionAlertTests(unittest.TestCase):
    def test_entry_alert_beeps_and_shows_greeting_popup(self):
        cli = _cli_for_role("entry")

        with patch.object(cli, "_play_recognition_beep") as beep:
            cli._maybe_trigger_recognition_alert({"name": "Ada Lovelace"}, now=100.0)

        beep.assert_called_once_with()
        self.assertEqual(cli._greeting_popup_name, "Ada Lovelace")
        self.assertEqual(cli._greeting_popup_active_until, 104.0)

    def test_exit_alert_beeps_without_greeting_popup(self):
        cli = _cli_for_role("exit")

        with patch.object(cli, "_play_recognition_beep") as beep:
            cli._maybe_trigger_recognition_alert({"name": "Ada Lovelace"}, now=100.0)

        beep.assert_called_once_with()
        self.assertEqual(cli._greeting_popup_name, "")
        self.assertEqual(cli._greeting_popup_active_until, 0.0)

    def test_alert_uses_same_user_cooldown(self):
        cli = _cli_for_role("exit")

        with patch.object(cli, "_play_recognition_beep") as beep:
            cli._maybe_trigger_recognition_alert({"name": "Ada Lovelace"}, now=100.0)
            cli._maybe_trigger_recognition_alert({"name": "Ada Lovelace"}, now=104.0)
            cli._maybe_trigger_recognition_alert({"name": "Ada Lovelace"}, now=108.0)

        self.assertEqual(beep.call_count, 2)

    def test_blocked_recognition_payload_keeps_track_recognized_for_display(self):
        cli = _cli_for_role("entry")
        track_state = TrackingState(failed_good_quality_attempts=2)
        result = {
            "status": "blocked",
            "reason_code": "already_inside",
            "payload": {"name": "Ada Lovelace", "sr_code": "SR-42"},
        }

        with patch.object(cli, "_play_recognition_beep") as beep:
            applied = cli._apply_identified_recognition_result(track_state, result, now=100.0)

        label, label_color = cli._build_identity_label(track_state, types.SimpleNamespace(), False)
        self.assertTrue(applied)
        self.assertTrue(track_state.recognized)
        self.assertEqual(track_state.user["name"], "Ada Lovelace")
        self.assertEqual(track_state.failed_good_quality_attempts, 0)
        self.assertEqual(label, "Recognized: Ada Lovelace")
        self.assertEqual(label_color, (0, 255, 0))
        beep.assert_called_once_with()

    def test_unmatched_track_logs_unrecognized_immediately(self):
        cli = _cli_for_role("entry")
        repo = _FakeRepository()
        track_state = TrackingState(last_quality_score=0.82)
        cli.repository = repo
        cli.state = _FakeState({7: track_state})

        logged = cli._log_unrecognized_detection(
            7,
            track_state,
            {"status": "no_match", "quality_score": 0.82},
            100.0,
        )

        self.assertTrue(logged)
        self.assertIsNotNone(track_state.unrecognized_event_id)
        self.assertTrue(track_state.unrecognized_logged)
        self.assertEqual(len(repo.logged), 1)
        self.assertEqual(repo.logged[0]["track_id"], 7)
        self.assertEqual(repo.logged[0]["method"], "immediate-unrecognized")

    def test_same_unmatched_track_logs_only_one_unrecognized_record(self):
        cli = _cli_for_role("entry")
        repo = _FakeRepository()
        track_state = TrackingState(last_quality_score=0.82)
        cli.repository = repo
        cli.state = _FakeState({7: track_state})

        cli._log_unrecognized_detection(
            7,
            track_state,
            {"status": "no_match", "quality_score": 0.82},
            100.0,
        )
        first_event_id = track_state.unrecognized_event_id
        cli._log_unrecognized_detection(
            7,
            track_state,
            {"status": "no_match", "quality_score": 0.84},
            101.0,
        )
        cli._log_unrecognized_detection(
            7,
            track_state,
            {"status": "no_match", "quality_score": 0.86},
            111.0,
        )

        self.assertEqual(repo.logged[0]["event_id"], first_event_id)
        self.assertEqual(len(repo.logged), 1)
        self.assertTrue(track_state.unrecognized_logged)

    def test_recognized_track_revokes_prior_unrecognized_log(self):
        cli = _cli_for_role("entry")
        repo = _FakeRepository()
        track_state = TrackingState(last_quality_score=0.82)
        cli.repository = repo
        cli.state = _FakeState({7: track_state})
        cli._log_unrecognized_detection(
            7,
            track_state,
            {"status": "no_match", "quality_score": 0.82},
            100.0,
        )
        unknown_event_id = track_state.unrecognized_event_id

        with patch.object(cli, "_play_recognition_beep"):
            applied = cli._apply_identified_recognition_result(
                track_state,
                {"status": "recognized", "payload": {"name": "Ada Lovelace", "sr_code": "SR-42"}},
                now=105.0,
            )

        self.assertTrue(applied)
        self.assertEqual(len(repo.logged), 1)
        self.assertEqual(len(repo.revoked), 1)
        self.assertEqual(repo.revoked[0]["event_id"], unknown_event_id)
        self.assertEqual(repo.revoked[0]["recognized_user"]["name"], "Ada Lovelace")
        self.assertIsNone(track_state.unrecognized_event_id)
        self.assertFalse(track_state.unrecognized_logged)

    def test_geometry_reset_clears_unrecognized_state(self):
        config = AppConfig()
        config.position_tolerance = 10
        state = AppStateManager(config)
        tracking_service = TrackingService(config, state)
        track_state = state.initialize_track_state(3, 100.0)
        track_state.last_bbox = (0, 0, 100, 100)
        track_state.unrecognized_event_id = "unknown-test"
        track_state.unrecognized_first_seen = 100.0
        track_state.unrecognized_logged = True
        track_state.unrecognized_face_quality = 0.8

        reset = tracking_service.refresh_track_geometry(3, (1000, 1000, 1100, 1100))

        self.assertTrue(reset)
        self.assertIsNone(track_state.unrecognized_event_id)
        self.assertEqual(track_state.unrecognized_first_seen, 0.0)
        self.assertFalse(track_state.unrecognized_logged)
        self.assertIsNone(track_state.unrecognized_face_quality)


if __name__ == "__main__":
    unittest.main()
