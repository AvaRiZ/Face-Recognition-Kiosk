import unittest
import sys
import types
from unittest.mock import patch

realtime_stub = types.ModuleType("app.realtime")
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
sys.modules.setdefault("app.realtime", realtime_stub)

from app.cli import CLIApplication


def _cli_for_role(worker_role: str) -> CLIApplication:
    cli = CLIApplication.__new__(CLIApplication)
    cli.worker_role = worker_role
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


if __name__ == "__main__":
    unittest.main()
