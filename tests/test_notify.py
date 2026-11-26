# -*- coding:utf-8 -*-

import base64
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yyds_notify_os.core as core_module
from yyds_notify_os.core import (
    _NotificationDispatcher,
    _resolve_icon_path,
    _run_command,
    _sanitize_string,
    _send_notification_sync,
    _which,
    notify,
    shutdown,
)


class TestNotifyOS(unittest.TestCase):
    def setUp(self):
        with core_module._linux_notify_send_profile_lock:
            core_module._linux_notify_send_state = (None, None)
        with core_module._which_cache_lock:
            core_module._which_cache.clear()
        with core_module._win_aumid_lock:
            core_module._win_aumid_cache.clear()
        self._reset_global_dispatcher()

    def tearDown(self):
        self._reset_global_dispatcher()

    @staticmethod
    def _reset_global_dispatcher():
        with core_module._dispatcher_lock:
            prev = core_module._dispatcher
            core_module._dispatcher = None
            core_module._shutdown_requested = False
            core_module._state_pid = os.getpid()
        if prev is not None:
            prev.shutdown(wait=True, timeout=1.0)

    def test_resolve_icon_path_files_dirs_tilde_and_themes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_file = os.path.join(tmpdir, "bell.png")
            with open(icon_file, "wb") as handle:
                handle.write(b"png")

            # Existing local file resolves to absolute path
            rel_name = os.path.relpath(icon_file)
            self.assertEqual(_resolve_icon_path(rel_name), os.path.abspath(icon_file))

            # Directories are rejected (not valid image files)
            self.assertIsNone(_resolve_icon_path(tmpdir))
            self.assertIsNone(_resolve_icon_path(os.path.relpath(tmpdir)))

            # Tilde user-home expansion
            with patch.dict(os.environ, {"HOME": tmpdir, "USERPROFILE": tmpdir}):
                self.assertEqual(
                    _resolve_icon_path("~/bell.png"), os.path.abspath(icon_file)
                )

        with patch("platform.system", return_value="Windows"):
            self.assertEqual(
                _resolve_icon_path("non_existent_file.png"), "non_existent_file.png"
            )

        with patch("platform.system", return_value="FreeBSD"):
            self.assertEqual(
                _resolve_icon_path("dialog-information"), "dialog-information"
            )

        self.assertIsNone(_resolve_icon_path(None))
        self.assertIsNone(_resolve_icon_path(""))

        with self.assertRaises(ValueError):
            _resolve_icon_path(b"bytes_path")
        with self.assertRaises(ValueError):
            _resolve_icon_path("bad\x00icon.png")

    def test_which_caches_stdlib_lookup_by_path(self):
        calls = []

        def fake_stdlib_which(name):
            calls.append(name)
            return f"/bin/{name}"

        with patch.object(core_module, "_STDLIB_WHICH", fake_stdlib_which):
            with patch("shutil.which", fake_stdlib_which):
                with patch.dict(os.environ, {"PATH": "/bin"}):
                    self.assertEqual(_which("notify-send"), "/bin/notify-send")
                    self.assertEqual(_which("notify-send"), "/bin/notify-send")
                    self.assertEqual(len(calls), 1)

                with patch.dict(os.environ, {"PATH": "/usr/bin"}):
                    self.assertEqual(_which("notify-send"), "/bin/notify-send")
                    self.assertEqual(len(calls), 2)

    def test_run_command_grandchild_pipe_does_not_hang_after_timeout(self):
        # Child spawns a grandchild holding stderr open for 5s, while timeout=0.1s
        script = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)']); "
            "time.sleep(5)"
        )
        t0 = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_command(
                [sys.executable, "-c", script],
                timeout=0.1,
                capture_stderr=True,
            )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0)

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
            notify("title", "message", timeout=True)
        with self.assertRaises(ValueError):
            notify("title", "message", timeout=1.5)
        with self.assertRaises(ValueError):
            notify("title", "message", sound=object())
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id="")
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id=True)
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id=-1)
        with self.assertRaises(ValueError):
            notify("title", "message", replace_id=object())
        with self.assertRaises(ValueError):
            notify("title", "message", icon=object())
        with self.assertRaises(ValueError):
            notify("title", "message", block="yes")
        with self.assertRaises(ValueError):
            notify("title", "message", fallback_to_print="no")

    def test_public_options_are_normalized_before_delivery(self):
        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            self.assertTrue(
                notify(
                    "Title",
                    "Message",
                    subtitle="   ",
                    icon=Path("missing-icon.png"),
                    urgency=" CRITICAL ",
                    sound="  Ping\nTone  ",
                    app_name="   ",
                    replace_id=" job\n1 ",
                    block=True,
                )
            )

        args = mock_send.call_args[0]
        self.assertIsNone(args[2])
        self.assertEqual(args[3], "missing-icon.png")
        self.assertEqual(args[4], "critical")
        self.assertEqual(args[6], "Ping Tone")
        self.assertEqual(args[7], "yyds-notify")
        self.assertEqual(args[8], "job 1")

        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            notify("Title", "Message", sound="   ", replace_id=42, block=True)
            self.assertFalse(mock_send.call_args[0][6])
            self.assertEqual(mock_send.call_args[0][8], 42)

    def test_sanitize_string_streaming_control_prefixes_whitespace_and_large_blobs(
        self,
    ):
        dirty = "Hello\x00\x07\x7f\x9b31m\r\nWorld"
        self.assertEqual(_sanitize_string(dirty, 64), "Hello31m\nWorld")

        # 600 leading null bytes must not prematurely slice away the real title
        null_prefixed = ("\x00" * 600) + "Real Title"
        self.assertEqual(_sanitize_string(null_prefixed, 128), "Real Title")

        # Long internal whitespace collapses cleanly before truncation
        spaced_app = "App" + (" " * 400) + "v2"
        self.assertEqual(
            _sanitize_string(spaced_app, 64, collapse_whitespace=True), "App v2"
        )

        huge = "X" * 2_000_000
        t0 = time.monotonic()
        truncated = _sanitize_string(huge, 1024)
        elapsed = time.monotonic() - t0
        self.assertEqual(len(truncated), 1024)
        self.assertTrue(truncated.endswith("..."))
        self.assertLess(elapsed, 0.2)

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_linux_notify_send_escapes_markup_supports_subtitle_sound_and_hyphens(
        self, mock_run, mock_which, mock_system
    ):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            res = _send_notification_sync(
                "-v",
                "File <stdin> & 1 < 2",
                subtitle="Sub <tag>",
                icon="dialog-information",
                urgency="critical",
                timeout=3,
                sound="alarm",
                app_name="TestApp",
                replace_id=123,
            )
            self.assertTrue(res)

        mock_which.assert_called_once_with("notify-send")
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args[0], "notify-send")
        self.assertIn("-t", called_args)
        self.assertIn("3000", called_args)
        self.assertIn("-u", called_args)
        self.assertIn("critical", called_args)
        self.assertIn("-a", called_args)
        self.assertIn("TestApp", called_args)
        self.assertIn("-i", called_args)
        self.assertIn("dialog-information", called_args)
        self.assertIn("-h", called_args)
        self.assertIn("string:sound-name:alarm-clock-elapsed", called_args)
        self.assertIn("-r", called_args)
        self.assertIn("123", called_args)
        sep_idx = called_args.index("--")
        self.assertEqual(
            called_args[sep_idx + 1 :],
            ["-v", "Sub &lt;tag&gt;\nFile &lt;stdin&gt; &amp; 1 &lt; 2"],
        )

    @patch("platform.system", return_value="FreeBSD")
    @patch("shutil.which", return_value="/usr/local/bin/notify-send")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_bsd_unix_uses_notify_send_and_skips_missing_slash_icon_path(
        self, mock_run, mock_which, mock_system
    ):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertTrue(
                _send_notification_sync(
                    "BSD",
                    "Works",
                    icon="assets/missing.png",
                    sound=True,
                    fallback_to_print=False,
                )
            )
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-i", cmd)
        self.assertIn("string:sound-name:message-new-instant", cmd)

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_linux_notify_send_replace_id_fallback(
        self, mock_run, mock_which, mock_system
    ):
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr=b"unknown option -r"),
            MagicMock(returncode=0),
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            res = _send_notification_sync(
                "MyTitle", "MyMessage", replace_id=456, fallback_to_print=False
            )
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 2)
        self.assertIn("-r", mock_run.call_args_list[0][0][0])
        self.assertNotIn("-r", mock_run.call_args_list[1][0][0])

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run")
    def test_linux_non_replace_call_does_not_poison_later_replace_fallback(
        self, mock_run, mock_which, mock_system
    ):
        def fake_notify_send(cmd, **kwargs):
            if "-r" in cmd:
                return MagicMock(returncode=1, stderr=b"Unknown option -r")
            return MagicMock(returncode=0, stderr=b"")

        mock_run.side_effect = fake_notify_send

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertTrue(
                _send_notification_sync("Basic", "First", fallback_to_print=False)
            )
            self.assertTrue(
                _send_notification_sync(
                    "Progress", "10%", replace_id="job", fallback_to_print=False
                )
            )
            count_before = mock_run.call_count
            self.assertTrue(
                _send_notification_sync("Basic", "Second", fallback_to_print=False)
            )
            self.assertTrue(
                _send_notification_sync(
                    "Progress", "20%", replace_id="job", fallback_to_print=False
                )
            )
            self.assertEqual(mock_run.call_count - count_before, 2)

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which")
    @patch("subprocess.run", return_value=MagicMock(returncode=1, stderr=b"failed"))
    def test_linux_notify_send_invalidates_failed_cached_profile(
        self, mock_run, mock_which, mock_system
    ):
        mock_which.side_effect = lambda name: (
            "/usr/bin/notify-send" if name == "notify-send" else None
        )
        with core_module._linux_notify_send_profile_lock:
            core_module._linux_notify_send_state = ("with_app", False)

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertFalse(
                _send_notification_sync(
                    "Title", "Message", replace_id="job", fallback_to_print=False
                )
            )

        mock_run.assert_called_once()
        with core_module._linux_notify_send_profile_lock:
            self.assertEqual(core_module._linux_notify_send_state, (None, None))

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_linux_dbus_session_without_display_uses_notify_send(
        self, mock_run, mock_which, mock_system
    ):
        mock_which.side_effect = lambda name: (
            "/usr/bin/notify-send" if name == "notify-send" else "/usr/bin/zenity"
        )
        with patch.dict(
            os.environ,
            {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
            clear=True,
        ):
            self.assertTrue(
                _send_notification_sync("CronTitle", "CronMsg", fallback_to_print=False)
            )
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0][0], "notify-send")

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which", return_value="/usr/bin/notify-send")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_linux_replace_id_fits_signed_int32_and_avoids_zero(
        self, mock_run, mock_which, mock_system
    ):
        tricky_ids = [
            0,
            "0",
            0xFFFFFFFF,
            "demo_progress_task",
            "cli_progress_task",
            "compile_task",
            "build_job_12",
            "my_task_id",
        ]
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            for rid in tricky_ids:
                mock_run.reset_mock()
                self.assertTrue(_send_notification_sync("T", "M", replace_id=rid))
                cmd = mock_run.call_args[0][0]
                r_val = int(cmd[cmd.index("-r") + 1])
                self.assertGreaterEqual(r_val, 1)
                self.assertLessEqual(r_val, 0x7FFFFFFF)

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_linux_zenity_icon_and_legacy_window_icon_fallback(
        self, mock_run, mock_which, mock_system
    ):
        mock_which.side_effect = lambda name: (
            "/usr/bin/zenity" if name == "zenity" else None
        )
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            res = _send_notification_sync(
                "Title & Co",
                "1 < 2",
                subtitle="sub > detail",
                icon="dialog-information",
                fallback_to_print=False,
            )
            self.assertTrue(res)

        self.assertEqual(mock_run.call_count, 2)
        first_cmd = mock_run.call_args_list[0][0][0]
        second_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("--icon=dialog-information", first_cmd)
        self.assertIn("--window-icon=dialog-information", second_cmd)
        self.assertIn(
            "--text=<b>Title &amp; Co</b>\n<i>sub &gt; detail</i>\n1 &lt; 2",
            first_cmd,
        )

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.run")
    @patch("sys.stderr.write")
    def test_headless_fallback_to_print_and_broken_stderr_resilience(
        self, mock_stderr, mock_run, mock_system
    ):
        with patch.dict(os.environ, {}, clear=True):
            res = _send_notification_sync(
                "Title", "Message", subtitle="Sub", fallback_to_print=True
            )
            self.assertFalse(res)
            mock_run.assert_not_called()
            written_str = mock_stderr.call_args[0][0]
            self.assertIn("\a[Notification] Title (Sub): Message", written_str)

            mock_stderr.side_effect = OSError("broken pipe")
            with patch("builtins.print") as mock_print:
                self.assertFalse(
                    _send_notification_sync("Title", "Message", fallback_to_print=True)
                )
                mock_print.assert_called_once()

    @patch("platform.system", return_value="Darwin")
    @patch("os.path.isfile", return_value=True)
    @patch("shutil.which", return_value="/usr/local/bin/terminal-notifier")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_mac_terminal_notifier_escapes_all_plist_prefixes_and_maps_sound(
        self, mock_run, mock_which, mock_isfile, mock_system
    ):
        self.assertTrue(
            _send_notification_sync(
                "\\server\\share",
                "<0f1a>",
                subtitle="'quoted'",
                icon="icon.png",
                sound="reminder",
                replace_id="[job]",
                fallback_to_print=False,
            )
        )

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-title") + 1], "\\\\server\\share")
        self.assertEqual(cmd[cmd.index("-message") + 1], "\\<0f1a>")
        self.assertEqual(cmd[cmd.index("-subtitle") + 1], "\\'quoted'")
        self.assertEqual(cmd[cmd.index("-group") + 1], "\\[job]")
        self.assertEqual(cmd[cmd.index("-sound") + 1], "Glass")
        self.assertIn("-appIcon", cmd)

    @patch("platform.system", return_value="Darwin")
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_mac_osascript_uses_argv_and_maps_default_sound(
        self, mock_run, mock_which, mock_system
    ):
        res = _send_notification_sync(
            "Hello", 'World "Quotes"', subtitle="Sub", sound="default"
        )
        self.assertTrue(res)
        mock_which.assert_called_once_with("terminal-notifier")
        mock_run.assert_called_once()

        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args[:2], ["osascript", "-e"])
        self.assertIn("on run argv", called_args[2])
        self.assertEqual(
            called_args[3:],
            ["--", "Hello", 'World "Quotes"', "Sub", "Tink"],
        )

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    @patch("os.path.isfile", return_value=True)
    def test_windows_powershell_toast_generic_and_aumid_registry_cache(
        self, mock_isfile, mock_run, mock_system
    ):
        long_tag = "x" * 80
        # 1st call: syncs registry (NOTIFY_SYNC_REG == "True")
        self.assertTrue(
            _send_notification_sync(
                "WinTitle",
                "WinMessage",
                subtitle="WinSub",
                sound="alarm",
                icon="logo.png",
                app_name="My/App",
                replace_id=long_tag,
            )
        )
        first_env = mock_run.call_args[1]["env"]
        self.assertEqual(first_env["NOTIFY_SYNC_REG"], "True")

        called_args = mock_run.call_args[0][0]
        enc_idx = called_args.index("-EncodedCommand")
        decoded_script = base64.b64decode(called_args[enc_idx + 1]).decode("utf-16le")
        self.assertIn("Windows.Data.Xml.Dom.XmlDocument", decoded_script)
        self.assertIn("ContentType = WindowsRuntime", decoded_script)
        self.assertIn('<binding template="ToastGeneric">', decoded_script)
        self.assertIn("Remove-ItemProperty", decoded_script)
        self.assertNotIn("ToastTemplateType]::ToastGeneric", decoded_script)
        self.assertEqual(
            first_env["NOTIFY_SOUND_SRC"],
            "ms-winsoundevent:Notification.Looping.Alarm",
        )
        self.assertEqual(first_env["NOTIFY_SOUND_LOOP"], "True")
        self.assertEqual(first_env["NOTIFY_DURATION"], "long")
        self.assertEqual(
            first_env["NOTIFY_TAG"],
            hashlib.sha256(long_tag.encode("utf-8")).hexdigest(),
        )

        # 2nd call with identical (app_name, icon): skips registry write!
        self.assertTrue(
            _send_notification_sync(
                "WinTitle2", "WinMessage2", icon="logo.png", app_name="My/App"
            )
        )
        self.assertEqual(mock_run.call_args[1]["env"]["NOTIFY_SYNC_REG"], "False")

        # 3rd call removing icon (`icon=None`): re-syncs registry to clear IconUri!
        self.assertTrue(
            _send_notification_sync(
                "WinTitle3", "WinMessage3", icon=None, app_name="My/App"
            )
        )
        self.assertEqual(mock_run.call_args[1]["env"]["NOTIFY_SYNC_REG"], "True")
        self.assertEqual(mock_run.call_args[1]["env"]["NOTIFY_ICON_PATH"], "")

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 10))
    def test_windows_timeout_returns_false(self, mock_run, mock_system):
        self.assertFalse(
            _send_notification_sync("Title", "Message", fallback_to_print=False)
        )

    def test_dispatcher_scales_up_on_burst_and_retires_idle_workers(self):
        dispatcher = _NotificationDispatcher(
            max_workers=4, max_pending=16, idle_timeout=0.08
        )
        base_args = (
            "Title",
            "Msg",
            None,
            None,
            "normal",
            5,
            False,
            "yyds-notify",
            None,
            False,
        )
        barrier_event = threading.Event()

        def work_item(*args):
            barrier_event.wait(timeout=1.0)
            return True

        try:
            with patch(
                "yyds_notify_os.core._send_notification_sync", return_value=True
            ):
                # Warm up 1 worker and wait slightly so it enters idle wait()
                self.assertTrue(dispatcher.submit(base_args))
                time.sleep(0.02)
                self.assertEqual(dispatcher._workers, 1)

            with patch(
                "yyds_notify_os.core._send_notification_sync", side_effect=work_item
            ):
                # Tight burst of 8 items must scale up to all 4 workers!
                for _ in range(8):
                    self.assertTrue(dispatcher.submit(base_args))
                self.assertEqual(dispatcher._workers, 4)
                barrier_event.set()

            # Wait for idle_timeout (0.08s) -> all 4 workers retire back to 0!
            deadline = time.monotonic() + 1.0
            while dispatcher._workers > 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(dispatcher._workers, 0)
        finally:
            barrier_event.set()
            dispatcher.shutdown(wait=True, timeout=1.0)

    def test_explicit_shutdown_wait_false_not_overridden_by_atexit(self):
        started = threading.Event()
        release = threading.Event()

        def slow_send(*args):
            started.set()
            release.wait(timeout=1.0)
            return True

        try:
            with patch(
                "yyds_notify_os.core._send_notification_sync", side_effect=slow_send
            ):
                self.assertTrue(notify("Title", "Slow", block=False))
                self.assertTrue(started.wait(timeout=1.0))
                shutdown(wait=False)

                # _atexit_cleanup must return immediately because shutdown was already called
                t0 = time.monotonic()
                core_module._atexit_cleanup()
                self.assertLess(time.monotonic() - t0, 0.1)
        finally:
            release.set()

    def test_lazy_async_dispatcher_and_shutdown(self):
        with patch("yyds_notify_os.core._send_notification_sync", return_value=True):
            self.assertTrue(notify("SyncTitle", "SyncMsg", block=True))
            self.assertIsNone(core_module._dispatcher)

        delivered = threading.Event()

        def fake_send(*args):
            delivered.set()
            return True

        with patch(
            "yyds_notify_os.core._send_notification_sync", side_effect=fake_send
        ) as mock_send:
            self.assertTrue(notify("AsyncTitle", "AsyncMsg", block=False))
            self.assertIsNotNone(core_module._dispatcher)
            self.assertTrue(delivered.wait(timeout=1.0))
            shutdown(wait=True, timeout=1.0)

        mock_send.assert_called_once()

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

        with patch(
            "yyds_notify_os.core._send_notification_sync", side_effect=fake_send
        ):
            self.assertTrue(notify("Progress", "first", replace_id="same"))
            self.assertTrue(first_started.wait(timeout=1))
            self.assertTrue(notify("Progress", "second", replace_id="same"))
            self.assertTrue(notify("Progress", "third", replace_id="same"))
            release_first.set()
            self.assertTrue(last_delivered.wait(timeout=1))
            shutdown(wait=True, timeout=1)

        self.assertEqual(delivered, ["first", "third"])

    def test_shutdown_wait_false_drops_queued_items_and_validates_args(self):
        with self.assertRaises(ValueError):
            shutdown(wait="yes")
        with self.assertRaises(ValueError):
            shutdown(wait=True, timeout=-1)
        with self.assertRaises(ValueError):
            shutdown(wait=True, timeout=True)

        first_started = threading.Event()
        release_first = threading.Event()
        delivered = []

        dispatcher = _NotificationDispatcher(max_workers=1, max_pending=8)

        def slow_send(title, message, *args):
            delivered.append(message)
            if message == "first":
                first_started.set()
                release_first.wait(timeout=1.0)
            return True

        base_args = (
            "Title",
            "first",
            None,
            None,
            "normal",
            5,
            False,
            "yyds-notify",
            None,
            False,
        )
        second_args = ("Title", "second") + base_args[2:]
        keyed_args = base_args[:1] + ("keyed",) + base_args[2:8] + ("k1", False)

        with patch(
            "yyds_notify_os.core._send_notification_sync", side_effect=slow_send
        ):
            self.assertTrue(dispatcher.submit(base_args))
            self.assertTrue(first_started.wait(timeout=1.0))
            self.assertTrue(dispatcher.submit(second_args))
            self.assertTrue(dispatcher.submit(keyed_args))
            dispatcher.shutdown(wait=False)
            release_first.set()
            dispatcher.shutdown(wait=True, timeout=1.0)

        self.assertEqual(delivered, ["first"])

    def test_async_queue_full_returns_false(self):
        blocker = threading.Event()
        started = threading.Event()
        dispatcher = _NotificationDispatcher(max_workers=1, max_pending=2)

        def block_send(*args):
            started.set()
            blocker.wait(timeout=1.0)
            return True

        sample_args = (
            "Title",
            "Msg",
            None,
            None,
            "normal",
            5,
            False,
            "yyds-notify",
            None,
            False,
        )
        keyed_args = sample_args[:8] + ("job", False)

        try:
            with patch(
                "yyds_notify_os.core._send_notification_sync", side_effect=block_send
            ):
                self.assertTrue(dispatcher.submit(sample_args))
                self.assertTrue(started.wait(timeout=1.0))
                self.assertTrue(dispatcher.submit(sample_args))
                self.assertFalse(dispatcher.submit(sample_args))
                self.assertFalse(dispatcher.submit(keyed_args))
        finally:
            blocker.set()
            dispatcher.shutdown(wait=True, timeout=1.0)

    def test_async_exception_is_logged_once_without_duplicate_warning(self):
        with self.assertLogs("yyds_notify_os", level="WARNING") as captured:
            with patch(
                "yyds_notify_os.core._send_notification_sync",
                side_effect=RuntimeError("background boom"),
            ):
                self.assertTrue(
                    notify(
                        "Title",
                        "Message",
                        replace_id="job_err",
                        block=False,
                        fallback_to_print=False,
                    )
                )
                shutdown(wait=True, timeout=1.0)

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "ERROR")
        self.assertIn("background boom", captured.output[0])

    def test_async_false_result_is_logged(self):
        with self.assertLogs("yyds_notify_os", level="WARNING") as captured:
            with patch(
                "yyds_notify_os.core._send_notification_sync",
                return_value=False,
            ):
                self.assertTrue(
                    notify("Title", "Message", block=False, fallback_to_print=False)
                )
                shutdown(wait=True, timeout=1.0)

        self.assertTrue(
            any("could not be delivered" in line for line in captured.output)
        )

    def test_async_after_shutdown_falls_back_to_sync(self):
        shutdown(wait=False)
        with patch(
            "yyds_notify_os.core._send_notification_sync", return_value=True
        ) as mock_send:
            self.assertTrue(notify("Title", "Message", block=False))

        mock_send.assert_called_once()

    def test_worker_spawn_failure_falls_back_to_sync(self):
        with patch.object(
            threading.Thread, "start", side_effect=RuntimeError("no threads")
        ):
            with patch(
                "yyds_notify_os.core._send_notification_sync", return_value=True
            ) as mock_send:
                self.assertTrue(notify("Title", "Message", block=False))

        mock_send.assert_called_once()

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX os.fork() required")
    def test_fork_in_child_resets_dispatcher_and_delivers_notifications(self):
        with patch("yyds_notify_os.core._send_notification_sync", return_value=True):
            self.assertTrue(notify("Parent", "Warmup", block=False))
            self.assertIsNotNone(core_module._dispatcher)
            time.sleep(0.05)

        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                with patch(
                    "yyds_notify_os.core._send_notification_sync", return_value=True
                ) as child_mock:
                    notify("ChildTitle", "ChildMsg", block=False)
                    shutdown(wait=True, timeout=1.0)
                    os.write(write_fd, str(child_mock.call_count).encode("ascii"))
            finally:
                os.close(write_fd)
                os._exit(0)
        else:
            os.close(write_fd)
            payload = os.read(read_fd, 16)
            os.close(read_fd)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(status, 0)
            self.assertEqual(payload, b"1")

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

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertFalse(
                _send_notification_sync(
                    "Title", "Message", replace_id="job", fallback_to_print=False
                )
            )

        mock_run.assert_called_once()

    def test_input_sanitization_and_truncation(self):
        long_title = "A" * 200
        long_message = "B" * 2000
        long_subtitle = "C" * 200
        long_app_name = "D" * 100

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
            args = mock_send.call_args[0]
            self.assertEqual(len(args[0]), 128)
            self.assertTrue(args[0].endswith("..."))
            self.assertEqual(len(args[1]), 1024)
            self.assertTrue(args[1].endswith("..."))
            self.assertEqual(len(args[2]), 128)
            self.assertTrue(args[2].endswith("..."))
            self.assertEqual(len(args[7]), 64)
            self.assertTrue(args[7].endswith("..."))

    @patch("platform.system", return_value="Linux")
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_linux_degrades_to_minimal_and_zenity_without_icon(
        self, mock_run, mock_which, mock_system
    ):
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr=b"err1"),
            MagicMock(returncode=1, stderr=b"err2"),
            MagicMock(returncode=0),
        ]
        logger = logging.getLogger("yyds_notify_os")
        prev_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
                self.assertTrue(
                    _send_notification_sync("T", "M", fallback_to_print=False)
                )
        finally:
            logger.setLevel(prev_level)
        self.assertEqual(
            mock_run.call_args_list[-1][0][0], ["notify-send", "--", "T", "M"]
        )

        with core_module._linux_notify_send_profile_lock:
            core_module._linux_notify_send_state = ("minimal", False)
        mock_run.side_effect = [
            OSError("exec failed"),
            subprocess.TimeoutExpired("zenity", 10),
        ]
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertFalse(_send_notification_sync("T", "M", fallback_to_print=False))

    @patch("platform.system", return_value="Darwin")
    @patch("shutil.which", return_value="/usr/local/bin/terminal-notifier")
    @patch("subprocess.run")
    def test_mac_terminal_notifier_failure_falls_back_to_osascript_and_handles_errors(
        self, mock_run, mock_which, mock_system
    ):
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr=b"tn error"),
            MagicMock(returncode=0),
        ]
        self.assertTrue(
            _send_notification_sync("Title", "Msg", sound=True, fallback_to_print=False)
        )
        self.assertEqual(mock_run.call_count, 2)

        mock_run.side_effect = [
            subprocess.TimeoutExpired("terminal-notifier", 10),
            subprocess.TimeoutExpired("osascript", 10),
        ]
        self.assertFalse(
            _send_notification_sync(
                "Title", "Msg", sound=False, fallback_to_print=False
            )
        )

        mock_run.side_effect = [
            OSError("missing tn"),
            MagicMock(returncode=1, stderr=b"osa err"),
        ]
        self.assertFalse(
            _send_notification_sync("Title", "Msg", fallback_to_print=False)
        )

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_windows_default_sound_and_oserror(self, mock_run, mock_system):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(
            _send_notification_sync(
                "T", "M", urgency="invalid_direct", sound=True, app_name=""
            )
        )
        env = mock_run.call_args[1]["env"]
        self.assertEqual(
            env["NOTIFY_SOUND_SRC"], "ms-winsoundevent:Notification.Default"
        )
        self.assertEqual(env["NOTIFY_CUSTOM_AUMID"], "")

        mock_run.side_effect = OSError("powershell missing")
        self.assertFalse(_send_notification_sync("T", "M", fallback_to_print=False))

    def test_unkeyed_async_exception_emits_fallback_and_shutdown_timeout_expires(self):
        with self.assertLogs("yyds_notify_os", level="ERROR"):
            with patch("sys.stderr.write") as mock_stderr:
                with patch(
                    "yyds_notify_os.core._send_notification_sync",
                    side_effect=RuntimeError("boom"),
                ):
                    self.assertTrue(
                        notify("Title", "Msg", block=False, fallback_to_print=True)
                    )
                    core_module._atexit_cleanup()
                mock_stderr.assert_called_once()

        self._reset_global_dispatcher()
        release = threading.Event()
        started = threading.Event()

        def hang_send(*args):
            started.set()
            release.wait(timeout=1.0)
            return True

        try:
            with patch(
                "yyds_notify_os.core._send_notification_sync", side_effect=hang_send
            ):
                self.assertTrue(notify("Title", "Slow", block=False))
                self.assertTrue(started.wait(timeout=1.0))
                t0 = time.monotonic()
                shutdown(wait=True, timeout=0.02)
                self.assertLess(time.monotonic() - t0, 0.5)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
