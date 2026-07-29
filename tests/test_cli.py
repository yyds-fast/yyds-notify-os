import argparse
import io
import logging
import subprocess
import unittest
from unittest.mock import patch

from yyds_notify_os import cli


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("yyds_notify_os")
        self.previous_handlers = list(self.logger.handlers)
        self.previous_level = self.logger.level
        self.previous_propagate = self.logger.propagate

    def tearDown(self):
        self.logger.handlers = self.previous_handlers
        self.logger.setLevel(self.previous_level)
        self.logger.propagate = self.previous_propagate

    @patch("yyds_notify_os.cli.notify", return_value=True)
    def test_main_sends_blocking_notification(self, mock_notify):
        result = cli.main(
            [
                "Title",
                "Message",
                "--sound",
                "sms",
                "--timeout",
                "3",
                "--app-name",
                "App",
            ]
        )

        self.assertEqual(result, 0)
        mock_notify.assert_called_once_with(
            title="Title",
            message="Message",
            subtitle=None,
            icon=None,
            urgency="normal",
            timeout=3,
            sound="sms",
            app_name="App",
            replace_id=None,
            block=True,
            fallback_to_print=True,
        )

    @patch("yyds_notify_os.cli.notify", return_value=False)
    def test_main_returns_failure(self, mock_notify):
        self.assertEqual(cli.main(["Title", "Message"]), 1)
        mock_notify.assert_called_once()

    @patch("yyds_notify_os.cli.notify")
    @patch("yyds_notify_os.cli._spawn_detached")
    def test_async_mode_spawns_child_without_sending_inline(
        self, mock_spawn, mock_notify
    ):
        args = ["Title", "Message", "--async", "--sound"]

        self.assertEqual(cli.main(args), 0)

        mock_spawn.assert_called_once_with(args)
        mock_notify.assert_not_called()

    @patch("yyds_notify_os.cli.subprocess.Popen")
    @patch.object(cli.sys, "platform", "linux")
    def test_spawn_detached_on_posix(self, mock_popen):
        cli._spawn_detached(["Title", "Message", "--async"])

        command = mock_popen.call_args[0][0]
        kwargs = mock_popen.call_args[1]
        self.assertNotIn("--async", command)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIsNone(kwargs["stderr"])

    @patch("yyds_notify_os.cli.subprocess.Popen")
    @patch.object(cli.sys, "platform", "linux")
    def test_spawn_preserves_literal_async_after_option_separator(self, mock_popen):
        cli._spawn_detached(["--async", "--", "Title", "--async"])

        command = mock_popen.call_args[0][0]
        self.assertEqual(command[-3:], ["--", "Title", "--async"])

    @patch("yyds_notify_os.cli.subprocess.Popen")
    @patch.object(cli.sys, "platform", "win32")
    def test_spawn_detached_on_windows(self, mock_popen):
        cli._spawn_detached(["Title", "Message", "--async"])

        kwargs = mock_popen.call_args[1]
        self.assertNotEqual(kwargs["creationflags"], 0)
        self.assertNotIn("start_new_session", kwargs)

    @patch("yyds_notify_os.cli._spawn_detached", side_effect=OSError("spawn failed"))
    def test_async_spawn_failure_is_reported(self, mock_spawn):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            result = cli.main(["Title", "Message", "--async"])

        self.assertEqual(result, 1)
        self.assertIn("spawn failed", stderr.getvalue())

    def test_cli_logging_replaces_library_null_handler(self):
        self.logger.handlers = [logging.NullHandler()]
        configured = cli._configure_logging()

        self.assertTrue(configured.handlers)
        self.assertFalse(
            any(isinstance(h, logging.NullHandler) for h in configured.handlers)
        )
        self.assertEqual(configured.level, logging.WARNING)
        self.assertFalse(configured.propagate)

    def test_timeout_must_be_positive(self):
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as captured:
                cli.main(["Title", "Message", "--timeout", "0"])

        self.assertEqual(captured.exception.code, 2)

    def test_timeout_must_be_an_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._positive_timeout("later")

    def test_async_option_does_not_accept_abbreviation(self):
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as captured:
                cli.main(["Title", "Message", "--asy"])

        self.assertEqual(captured.exception.code, 2)

    @patch("yyds_notify_os.cli.notify", side_effect=ValueError("bad option"))
    def test_validation_error_is_reported(self, mock_notify):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            result = cli.main(["Title", "Message"])

        self.assertEqual(result, 1)
        self.assertIn("bad option", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
