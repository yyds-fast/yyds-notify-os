# -*- coding:utf-8 -*-

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import platform
import base64

# Add the parent directory to Python path to import yyds_notify_os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from yyds_notify_os.core import notify, _escape_applescript, _send_notification_sync, _resolve_icon_path


class TestNotifyOS(unittest.TestCase):

    def test_escape_applescript(self):
        self.assertEqual(_escape_applescript('hello "world"'), 'hello \\"world\\"')
        self.assertEqual(_escape_applescript('back\\slash'), 'back\\\\slash')
        self.assertEqual(_escape_applescript(''), '')
        self.assertEqual(_escape_applescript(None), '')

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

    @patch("platform.system")
    @patch("subprocess.run")
    def test_linux_notify_send_success(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which notify-send
            MagicMock(returncode=0),  # notify-send with -r
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync(
                "MyTitle", "MyMessage", urgency="critical", timeout=3, app_name="TestApp", replace_id=123
            )
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 2)
        called_args = mock_run.call_args_list[1][0][0]
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
    @patch("subprocess.run")
    def test_linux_notify_send_replace_id_fallback(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which notify-send
            MagicMock(returncode=1),  # notify-send with -r (fails)
            MagicMock(returncode=0),  # fallback notify-send without -r (succeeds)
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync("MyTitle", "MyMessage", replace_id=456, fallback_to_print=False)
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 3)
        called_args_r = mock_run.call_args_list[1][0][0]
        called_args_fallback = mock_run.call_args_list[2][0][0]
        self.assertIn("-r", called_args_r)
        self.assertNotIn("-r", called_args_fallback)

    @patch("platform.system")
    @patch("subprocess.run")
    def test_linux_zenity_fallback(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            Exception("not found"),   # which notify-send
            MagicMock(returncode=0),  # which zenity
            MagicMock(returncode=0),  # zenity command
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync("MyTitle", "MyMessage", fallback_to_print=False)
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 3)
        called_args = mock_run.call_args_list[2][0][0]
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
    @patch("subprocess.run")
    def test_mac_terminal_notifier_success(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which terminal-notifier succeeds
            MagicMock(returncode=0),  # terminal-notifier command succeeds
        ]

        res = _send_notification_sync(
            "Hello", 'World "Quotes"', subtitle="Sub", sound=True
        )
        self.assertTrue(res)
        self.assertEqual(mock_run.call_count, 2)
        
        # Verify first call checked terminal-notifier
        called_args_first = mock_run.call_args_list[0][0][0]
        self.assertEqual(called_args_first, ["which", "terminal-notifier"])

        # Verify second call used terminal-notifier
        called_args_second = mock_run.call_args_list[1][0][0]
        self.assertEqual(called_args_second[0], "terminal-notifier")
        self.assertIn("-title", called_args_second)
        self.assertIn("Hello", called_args_second)
        self.assertIn("-message", called_args_second)
        self.assertIn('World "Quotes"', called_args_second)
        self.assertIn("-subtitle", called_args_second)
        self.assertIn("Sub", called_args_second)
        self.assertIn("-sound", called_args_second)
        self.assertIn("default", called_args_second)

    @patch("platform.system")
    @patch("subprocess.run")
    def test_mac_osascript_fallback(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.side_effect = [
            Exception("not found"),   # which terminal-notifier fails
            MagicMock(returncode=0),  # osascript succeeds
        ]

        res = _send_notification_sync(
            "Hello", 'World "Quotes"', subtitle="Sub", sound=True
        )
        self.assertTrue(res)
        self.assertEqual(mock_run.call_count, 2)
        
        called_args_first = mock_run.call_args_list[0][0][0]
        self.assertEqual(called_args_first, ["which", "terminal-notifier"])

        called_args_second = mock_run.call_args_list[1][0][0]
        called_kwargs_second = mock_run.call_args_list[1][1]
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

        res = _send_notification_sync("WinTitle", "WinMessage", sound="sms", icon="logo.png", replace_id="task_12")
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

    @patch("yyds_notify_os.core._executor")
    def test_async_non_blocking(self, mock_executor):
        mock_future = MagicMock()
        mock_executor.submit.return_value = mock_future

        res = notify("Title", "Message", block=False)
        self.assertTrue(res)
        mock_executor.submit.assert_called_once()
        called_args = mock_executor.submit.call_args[0]
        self.assertEqual(called_args[0], _send_notification_sync)
        self.assertEqual(called_args[1], "Title")
        self.assertEqual(called_args[2], "Message")

    @patch("platform.system")
    @patch("subprocess.run")
    def test_linux_zenity_markup_escaping(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            Exception("not found"),   # which notify-send
            MagicMock(returncode=0),  # which zenity
            MagicMock(returncode=0),  # zenity command
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync("Title & Co", "1 < 2", subtitle="sub > detail", fallback_to_print=False)
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 3)
        called_args = mock_run.call_args_list[2][0][0]
        self.assertIn("zenity", called_args)
        self.assertIn("--notification", called_args)
        self.assertIn("--text=<b>Title &amp; Co</b>\n<i>sub &gt; detail</i>\n1 &lt; 2", called_args)

    @patch("platform.system")
    @patch("subprocess.run")
    def test_linux_notify_send_string_replace_id(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which notify-send
            MagicMock(returncode=0),  # notify-send with -r
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            res = _send_notification_sync("T", "M", replace_id="my_task_id")
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 2)
        called_args = mock_run.call_args_list[1][0][0]
        self.assertIn("-r", called_args)
        
        import hashlib
        h = hashlib.md5(b"my_task_id").hexdigest()
        expected_id = int(h[:7], 16) + 1
        self.assertIn(str(expected_id), called_args)

    def test_input_sanitization_and_truncation(self):
        # Extremely long title and message
        long_title = "A" * 200
        long_message = "B" * 2000
        long_subtitle = "C" * 200
        long_app_name = "D" * 100

        # We want to patch _send_notification_sync to inspect what clean inputs it gets
        with patch("yyds_notify_os.core._send_notification_sync", return_value=True) as mock_send:
            res = notify(
                title=long_title,
                message=long_message,
                subtitle=long_subtitle,
                app_name=long_app_name,
                block=True
            )
            self.assertTrue(res)
            mock_send.assert_called_once()
            args = mock_send.call_args[0]
            
            # Verify truncation
            self.assertEqual(len(args[0]), 128 + 3) # 128 chars + "..."
            self.assertTrue(args[0].endswith("..."))
            self.assertEqual(len(args[1]), 1024 + 3) # 1024 chars + "..."
            self.assertTrue(args[1].endswith("..."))
            self.assertEqual(len(args[2]), 128 + 3) # 128 chars + "..."
            self.assertTrue(args[2].endswith("..."))
            self.assertEqual(len(args[7]), 64 + 3) # 64 chars + "..."
            self.assertTrue(args[7].endswith("..."))

    @patch("yyds_notify_os.core._executor")
    def test_notify_fallback_when_executor_shutdown(self, mock_executor):
        # Simulate ThreadPoolExecutor raising RuntimeError (already shut down)
        mock_executor.submit.side_effect = RuntimeError("Shutdown")
        
        with patch("yyds_notify_os.core._send_notification_sync", return_value=True) as mock_send:
            res = notify("Title", "Message", block=False)
            self.assertTrue(res)
            # Should fallback to synchronous call
            mock_send.assert_called_once_with(
                "Title", "Message", None, None, "normal", 5, False, "yyds-notify", None, True
            )


if __name__ == "__main__":
    unittest.main()
