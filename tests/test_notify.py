# -*- coding:utf-8 -*-

import base64
import hashlib
import os
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import yyds_notify_os.core as core_module
from yyds_notify_os.core import (
    _resolve_icon_path,
    _send_notification_sync,
    notify,
)


class TestNotifyOS(unittest.TestCase):
    @patch("os.path.exists")
    def test_resolve_icon_path(self, mock_exists):
        # 1. File exists locally
        mock_exists.return_value = True
        resolved = _resolve_icon_path("myicon.png")
        self.assertTrue(os.path.isabs(resolved))

        # 2. File does not exist, not Linux -> returns as-is
        mock_exists.return_value = False
        with patch("platform.system", return_value="Windows"):
            resolved = _resolve_icon_path("non_existent_file.png")
            self.assertEqual(resolved, "non_existent_file.png")

        # 3. Linux system icon name (no slashes) -> returns as-is
        with patch("platform.system", return_value="Linux"):
            resolved = _resolve_icon_path("dialog-information")
            self.assertEqual(resolved, "dialog-information")

    def test_notify_invalid_inputs(self):
        with self.assertRaises(ValueError):
            notify("", "message")
        with self.assertRaises(ValueError):
            notify("title", "")
        with self.assertRaises(ValueError):
            notify(None, "message")
        with self.assertRaises(ValueError):
            notify("title", "message", urgency=None)
        with self.assertRaises(ValueError):
            notify("title", "message", urgency="urgent")
        with self.assertRaises(ValueError):
            notify("title", "message", timeout=0)
        with self.assertRaises(ValueError):
            notify("title", "message", timeout=1.5)
        with self.assertRaises(ValueError):
            notify("title", "message", sound=object())
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id="")
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id=True)
        with self.assertRaises(ValueError):
            notify("title", "message", icon=object())

    def test_public_options_are_normalized_before_delivery(self):
        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            self.assertTrue(
                notify(
                    "Title",
                    "Message",
                    icon=Path("missing-icon.png"),
                    urgency=" CRITICAL ",
                    sound="  Ping\nTone  ",
                    app_name=" Demo\nApp ",
                    replace_id=" job\n1 ",
                    block=True,
                )
            )

        args = mock_send.call_args[0]
        self.assertEqual(args[3], "missing-icon.png")
        self.assertEqual(args[4], "critical")
        self.assertEqual(args[6], "Ping Tone")
        self.assertEqual(args[7], "Demo App")
        self.assertEqual(args[8], "job 1")

    @patch("platform.system")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_linux_notify_send_success(self, mock_run, mock_which, mock_system):
        mock_system.return_value = "Linux"
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync(
                "MyTitle",
                "MyMessage",
                urgency="critical",
                timeout=3,
                app_name="TestApp",
                replace_id=123,
            )
            self.assertTrue(res)

        mock_which.assert_called_once_with("notify-send")
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertIn("notify-send", called_args)
        self.assertIn("MyTitle", called_args)
        self.assertIn("MyMessage", called_args)
        self.assertIn("-t", called_args)
        self.assertIn("3000", called_args)
        self.assertIn("-u", called_args)
        self.assertIn("critical", called_args)
        self.assertIn("-a", called_args)
        self.assertIn("TestApp", called_args)
        self.assertIn("-r", called_args)
        self.assertIn("123", called_args)

    @patch("platform.system")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_linux_notify_send_replace_id_fallback(
        self, mock_run, mock_which, mock_system
    ):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            MagicMock(returncode=1),  # notify-send with -r (fails)
            MagicMock(returncode=0),  # fallback notify-send without -r (succeeds)
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync(
                "MyTitle", "MyMessage", replace_id=456, fallback_to_print=False
            )
            self.assertTrue(res)

        mock_which.assert_called_once_with("notify-send")
        self.assertEqual(mock_run.call_count, 2)
        called_args_r = mock_run.call_args_list[0][0][0]
        called_args_fallback = mock_run.call_args_list[1][0][0]
        self.assertIn("-r", called_args_r)
        self.assertNotIn("-r", called_args_fallback)

    @patch("platform.system")
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_linux_zenity_fallback(self, mock_run, mock_which, mock_system):
        mock_system.return_value = "Linux"
        mock_which.side_effect = lambda name: (
            "/usr/bin/zenity" if name == "zenity" else None
        )
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync(
                "MyTitle", "MyMessage", fallback_to_print=False
            )
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 1)
        called_args = mock_run.call_args[0][0]
        self.assertIn("zenity", called_args)
        self.assertIn("--notification", called_args)
        self.assertIn("--text=<b>MyTitle</b>\nMyMessage", called_args)

    @patch("platform.system")
    @patch("subprocess.run")
    @patch("sys.stderr.write")
    def test_headless_fallback_to_print(self, mock_stderr, mock_run, mock_system):
        mock_system.return_value = "Linux"

        with patch.dict(os.environ, {}, clear=True):
            res = _send_notification_sync("Title", "Message", fallback_to_print=True)
            self.assertFalse(res)
            mock_run.assert_not_called()
            mock_stderr.assert_called_once()
            written_str = mock_stderr.call_args[0][0]
            self.assertIn("\a[Notification] Title: Message", written_str)

    @patch("platform.system")
    @patch("shutil.which", return_value="/usr/local/bin/terminal-notifier")
    @patch("subprocess.run")
    def test_mac_terminal_notifier_success(self, mock_run, mock_which, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = MagicMock(returncode=0)

        res = _send_notification_sync(
            "Hello", 'World "Quotes"', subtitle="Sub", sound=True
        )
        self.assertTrue(res)
        mock_which.assert_called_once_with("terminal-notifier")
        mock_run.assert_called_once()

        called_args_second = mock_run.call_args[0][0]
        self.assertEqual(called_args_second[0], "terminal-notifier")
        self.assertIn("-title", called_args_second)
        self.assertIn("Hello", called_args_second)
        self.assertIn("-message", called_args_second)
        self.assertIn('World "Quotes"', called_args_second)
        self.assertIn("-subtitle", called_args_second)
        self.assertIn("Sub", called_args_second)
        self.assertIn("-sound", called_args_second)
        self.assertIn("default", called_args_second)

    @patch("platform.system", return_value="Darwin")
    @patch("os.path.exists", return_value=True)
    @patch("shutil.which", return_value="/usr/local/bin/terminal-notifier")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_mac_terminal_notifier_replacement_and_icon(
        self, mock_run, mock_which, mock_exists, mock_system
    ):
        self.assertTrue(
            _send_notification_sync(
                "Title",
                "Message",
                icon="icon.png",
                replace_id="progress",
                fallback_to_print=False,
            )
        )

        command = mock_run.call_args[0][0]
        self.assertIn("-group", command)
        self.assertIn("progress", command)
        self.assertIn("-appIcon", command)

    @patch("platform.system")
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_mac_osascript_fallback(self, mock_run, mock_which, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = MagicMock(returncode=0)

        res = _send_notification_sync(
            "Hello", 'World "Quotes"', subtitle="Sub", sound=True
        )
        self.assertTrue(res)
        mock_which.assert_called_once_with("terminal-notifier")
        mock_run.assert_called_once()

        called_args_second = mock_run.call_args[0][0]
        called_kwargs_second = mock_run.call_args[1]
        self.assertEqual(called_args_second[0], "osascript")
        self.assertEqual(called_args_second[1], "-e")
        self.assertIn("system attribute", called_args_second[2])
        self.assertIn("display notification", called_args_second[2])

        env = called_kwargs_second["env"]
        self.assertEqual(env["NOTIFY_TITLE"], "Hello")
        self.assertEqual(env["NOTIFY_MESSAGE"], 'World "Quotes"')
        self.assertEqual(env["NOTIFY_SUBTITLE"], "Sub")
        self.assertEqual(env["NOTIFY_SOUND"], "Tink")

    @patch("platform.system")
    @patch("subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_windows_powershell(self, mock_exists, mock_run, mock_system):
        mock_system.return_value = "Windows"
        mock_run.return_value = MagicMock(returncode=0)

        res = _send_notification_sync(
            "WinTitle", "WinMessage", sound="sms", icon="logo.png", replace_id="task_12"
        )
        self.assertTrue(res)

        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args[0], "powershell")
        self.assertEqual(called_args[3], "-EncodedCommand")

        encoded_data = called_args[4]
        decoded_script = base64.b64decode(encoded_data).decode("utf-16le")
        # Check ToastGeneric template check & replace tag & app_name notifier
        self.assertIn("ToastGeneric", decoded_script)
        self.assertIn("appLogoOverride", decoded_script)
        self.assertIn("$toast.Tag = $tag", decoded_script)
        self.assertIn("CreateToastNotifier($app_name)", decoded_script)

        env = mock_run.call_args[1]["env"]
        self.assertEqual(env["NOTIFY_TITLE"], "WinTitle")
        self.assertEqual(env["NOTIFY_MESSAGE"], "WinMessage")
        self.assertEqual(env["NOTIFY_APP_NAME"], "yyds-notify")
        self.assertEqual(env["NOTIFY_SOUND_SRC"], "ms-winsoundevent:Notification.SMS")
        self.assertEqual(env["NOTIFY_SILENT"], "False")
        self.assertEqual(env["NOTIFY_TAG"], "task_12")
        self.assertTrue(os.path.isabs(env["NOTIFY_ICON_PATH"]))

    def test_async_non_blocking(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch("yyds_notify_os.core._executor", executor):
                with patch(
                    "yyds_notify_os.core._send_notification_sync", return_value=True
                ) as mock_send:
                    res = notify("Title", "Message", block=False)

        self.assertTrue(res)
        mock_send.assert_called_once_with(
            "Title",
            "Message",
            None,
            None,
            "normal",
            5,
            False,
            "yyds-notify",
            None,
            True,
        )

    def test_async_same_replace_id_is_serialized_and_coalesced(self):
        first_started = threading.Event()
        release_first = threading.Event()
        last_delivered = threading.Event()
        delivered = []

        def fake_send(title, message, *args):
            delivered.append(message)
            if message == "first":
                first_started.set()
                release_first.wait(timeout=1)
            if message == "third":
                last_delivered.set()
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            with patch("yyds_notify_os.core._executor", executor):
                with patch(
                    "yyds_notify_os.core._send_notification_sync", side_effect=fake_send
                ):
                    self.assertTrue(notify("Progress", "first", replace_id="same"))
                    self.assertTrue(first_started.wait(timeout=1))
                    self.assertTrue(notify("Progress", "second", replace_id="same"))
                    self.assertTrue(notify("Progress", "third", replace_id="same"))
                    release_first.set()
                    self.assertTrue(last_delivered.wait(timeout=1))

        self.assertEqual(delivered, ["first", "third"])

    def test_async_exception_is_logged(self):
        with self.assertLogs("yyds_notify_os", level="ERROR") as captured:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with patch("yyds_notify_os.core._executor", executor):
                    with patch(
                        "yyds_notify_os.core._send_notification_sync",
                        side_effect=RuntimeError("background boom"),
                    ):
                        self.assertTrue(
                            notify(
                                "Title", "Message", block=False, fallback_to_print=False
                            )
                        )

        self.assertTrue(any("background boom" in line for line in captured.output))

    @patch("yyds_notify_os.core._pending_slots")
    def test_async_queue_full_returns_false(self, mock_slots):
        mock_slots.acquire.return_value = False

        result = notify("Title", "Message", block=False, fallback_to_print=False)

        self.assertFalse(result)
        mock_slots.acquire.assert_called_once_with(blocking=False)

    @patch("yyds_notify_os.core._pending_slots")
    def test_keyed_async_queue_full_returns_false(self, mock_slots):
        mock_slots.acquire.return_value = False

        result = notify(
            "Title", "Message", replace_id="job", block=False, fallback_to_print=False
        )

        self.assertFalse(result)
        self.assertNotIn("job", core_module._keyed_jobs)

    def test_async_after_shutdown_falls_back_to_sync(self):
        with patch("yyds_notify_os.core._executor_shutdown", True):
            with patch(
                "yyds_notify_os.core._send_notification_sync", return_value=True
            ) as mock_send:
                self.assertTrue(notify("Title", "Message", block=False))

        mock_send.assert_called_once()

    @patch("platform.system", return_value="Plan9")
    @patch("subprocess.run")
    def test_unsupported_platform_skips_subprocess(self, mock_run, mock_system):
        self.assertFalse(
            _send_notification_sync("Title", "Message", fallback_to_print=False)
        )
        mock_run.assert_not_called()

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("notify-send", 10))
    def test_linux_timeout_does_not_repeat_same_hung_command(
        self, mock_run, mock_which, mock_system
    ):
        mock_which.side_effect = lambda name: (
            "/usr/bin/notify-send" if name == "notify-send" else None
        )

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            self.assertFalse(
                _send_notification_sync(
                    "Title", "Message", replace_id="job", fallback_to_print=False
                )
            )

        mock_run.assert_called_once()

    @patch("platform.system")
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_linux_zenity_markup_escaping(self, mock_run, mock_which, mock_system):
        mock_system.return_value = "Linux"
        mock_which.side_effect = lambda name: (
            "/usr/bin/zenity" if name == "zenity" else None
        )
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync(
                "Title & Co", "1 < 2", subtitle="sub > detail", fallback_to_print=False
            )
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 1)
        called_args = mock_run.call_args[0][0]
        self.assertIn("zenity", called_args)
        self.assertIn("--notification", called_args)
        self.assertIn(
            "--text=<b>Title &amp; Co</b>\n<i>sub &gt; detail</i>\n1 &lt; 2",
            called_args,
        )

    @patch("platform.system")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_linux_notify_send_string_replace_id(
        self, mock_run, mock_which, mock_system
    ):
        mock_system.return_value = "Linux"
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync("T", "M", replace_id="my_task_id")
            self.assertTrue(res)

        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertIn("-r", called_args)

        h = hashlib.sha256(b"my_task_id").hexdigest()
        expected_id = int(h[:8], 16) or 1
        self.assertIn(str(expected_id), called_args)

    def test_input_sanitization_and_truncation(self):
        # Extremely long title and message
        long_title = "A" * 200
        long_message = "B" * 2000
        long_subtitle = "C" * 200
        long_app_name = "D" * 100

        # We want to patch _send_notification_sync to inspect what clean inputs it gets
        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            res = notify(
                title=long_title,
                message=long_message,
                subtitle=long_subtitle,
                app_name=long_app_name,
                block=True,
            )
            self.assertTrue(res)
            mock_send.assert_called_once()
            args = mock_send.call_args[0]

            # Verify truncation
            self.assertEqual(len(args[0]), 128)
            self.assertTrue(args[0].endswith("..."))
            self.assertEqual(len(args[1]), 1024)
            self.assertTrue(args[1].endswith("..."))
            self.assertEqual(len(args[2]), 128)
            self.assertTrue(args[2].endswith("..."))
            self.assertEqual(len(args[7]), 64)
            self.assertTrue(args[7].endswith("..."))

    @patch("yyds_notify_os.core._executor")
    def test_notify_fallback_when_executor_shutdown(self, mock_executor):
        # Simulate ThreadPoolExecutor raising RuntimeError (already shut down)
        mock_executor.submit.side_effect = RuntimeError("Shutdown")

        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            res = notify("Title", "Message", block=False)
            self.assertTrue(res)
            # Should fallback to synchronous call
            mock_send.assert_called_once_with(
                "Title",
                "Message",
                None,
                None,
                "normal",
                5,
                False,
                "yyds-notify",
                None,
                True,
            )


if __name__ == "__main__":
    unittest.main()
