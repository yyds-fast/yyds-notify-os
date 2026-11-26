# -*- coding:utf-8 -*-

from __future__ import annotations

import atexit
import base64
import hashlib
import html
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple, Union

logger = logging.getLogger("yyds_notify_os")
logger.addHandler(logging.NullHandler())

PathLikeStr = Union[str, "os.PathLike[str]"]
SoundOption = Union[bool, str]
ReplaceIdOption = Union[str, int]
_NotificationArgs = Tuple[
    str,
    str,
    Optional[str],
    Optional[str],
    str,
    int,
    SoundOption,
    str,
    Optional[ReplaceIdOption],
    bool,
]

_MAX_WORKERS = 4
_MAX_PENDING_NOTIFICATIONS = 64
_WORKER_IDLE_TIMEOUT = 60.0
_HARD_SCAN_CAP = 65536
_INT32_MAX = 0x7FFFFFFF
_UINT32_MAX = 0xFFFFFFFF

_UNIX_NOTIFY_SYSTEMS = frozenset(("Linux", "FreeBSD", "OpenBSD", "NetBSD"))

_STDLIB_WHICH = shutil.which
_STDLIB_SUBPROCESS_RUN = subprocess.run
_which_cache: dict = {}
_which_cache_lock = threading.Lock()

# `(base_profile, replace_supported)`:
# - base_profile: None | "with_app" | "without_app" | "minimal"
# - replace_supported: None (untested) | True | False
_linux_notify_send_state: Tuple[Optional[str], Optional[bool]] = (None, None)
_linux_notify_send_profile_lock = threading.Lock()

# Cache `(app_name, icon_path)` already synced to `HKCU\...\AppUserModelId\<aumid>`
# so repeated notifications with unchanged metadata skip registry writes.
_win_aumid_cache: Dict[str, Tuple[str, str]] = {}
_win_aumid_lock = threading.Lock()

# Windows sound events mapping
WIN_SOUND_MAP = {
    "default": "ms-winsoundevent:Notification.Default",
    "im": "ms-winsoundevent:Notification.IM",
    "mail": "ms-winsoundevent:Notification.Mail",
    "reminder": "ms-winsoundevent:Notification.Reminder",
    "sms": "ms-winsoundevent:Notification.SMS",
    "alarm": "ms-winsoundevent:Notification.Looping.Alarm",
    "call": "ms-winsoundevent:Notification.Looping.Call",
}
_WIN_LOOPING_SOUNDS = frozenset(("alarm", "call"))

# Cross-platform sound aliases mapped to built-in macOS `/System/Library/Sounds`
MAC_SOUND_MAP = {
    "default": "Tink",
    "im": "Pop",
    "mail": "Ping",
    "reminder": "Glass",
    "sms": "Glass",
    "alarm": "Sosumi",
    "call": "Submarine",
}

# Cross-platform sound aliases mapped to FreeDesktop XDG sound theme names
LINUX_SOUND_MAP = {
    "default": "message-new-instant",
    "im": "message-new-instant",
    "mail": "message-new-email",
    "reminder": "bell",
    "sms": "message-new-instant",
    "alarm": "alarm-clock-elapsed",
    "call": "phone-incoming-call",
}

_TERMINAL_NOTIFIER_ESCAPE_PREFIXES = ("\\", "-", "[", "(", "{", "<", '"', "'")

_WIN_POWERSHELL_APP_ID = (
    "{1AC14E77-C8E7-45C6-87F6-C512F4342091}\\WindowsPowerShell\\v1.0\\powershell.exe"
)

# Pre-encoded static PowerShell payload. All dynamic user inputs are read from
# process-scoped environment variables (`$env:NOTIFY_*`) and inserted via XML
# DOM text nodes / attributes so shell and XML injection cannot occur.
_WIN_PS_SCRIPT = """
$ErrorActionPreference = 'Stop'
$title = $env:NOTIFY_TITLE
$subtitle = $env:NOTIFY_SUBTITLE
$message = $env:NOTIFY_MESSAGE
$app_name = $env:NOTIFY_APP_NAME
$custom_aumid = $env:NOTIFY_CUSTOM_AUMID
$sync_reg = $env:NOTIFY_SYNC_REG
$app_id = $env:NOTIFY_APP_ID
$icon_path = $env:NOTIFY_ICON_PATH
$sound_src = $env:NOTIFY_SOUND_SRC
$sound_loop = $env:NOTIFY_SOUND_LOOP
$silent = $env:NOTIFY_SILENT
$duration = $env:NOTIFY_DURATION
$tag = $env:NOTIFY_TAG

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

    $notifier_id = $app_id
    $use_attribution = $false
    if ($custom_aumid -and $app_name) {
        if ($sync_reg -eq 'True') {
            try {
                $regPath = "HKCU:\\SOFTWARE\\Classes\\AppUserModelId\\$custom_aumid"
                if (-not (Test-Path -LiteralPath $regPath)) {
                    [void](New-Item -Path $regPath -Force)
                }
                Set-ItemProperty -LiteralPath $regPath -Name 'DisplayName' -Value $app_name -Type String -Force
                if ($icon_path) {
                    Set-ItemProperty -LiteralPath $regPath -Name 'IconUri' -Value $icon_path -Type String -Force
                } else {
                    Remove-ItemProperty -LiteralPath $regPath -Name 'IconUri' -ErrorAction SilentlyContinue
                }
                $notifier_id = $custom_aumid
            } catch {
                $notifier_id = $app_id
                $use_attribution = $true
            }
        } else {
            $notifier_id = $custom_aumid
        }
    }

    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml('<toast><visual><binding template="ToastGeneric"></binding></visual></toast>')
    $toastNode = $xml.DocumentElement
    if ($duration) {
        $toastNode.SetAttribute('duration', $duration)
    }

    $binding = $xml.GetElementsByTagName('binding').Item(0)

    $titleNode = $xml.CreateElement('text')
    [void]$titleNode.AppendChild($xml.CreateTextNode($title))
    [void]$binding.AppendChild($titleNode)

    if ($subtitle) {
        $subNode = $xml.CreateElement('text')
        [void]$subNode.AppendChild($xml.CreateTextNode($subtitle))
        [void]$binding.AppendChild($subNode)
    }

    $msgNode = $xml.CreateElement('text')
    [void]$msgNode.AppendChild($xml.CreateTextNode($message))
    [void]$binding.AppendChild($msgNode)

    if ($use_attribution -and $app_name) {
        $attrNode = $xml.CreateElement('text')
        $attrNode.SetAttribute('placement', 'attribution')
        [void]$attrNode.AppendChild($xml.CreateTextNode($app_name))
        [void]$binding.AppendChild($attrNode)
    }

    if ($icon_path) {
        $imageNode = $xml.CreateElement('image')
        $imageNode.SetAttribute('placement', 'appLogoOverride')
        $imageNode.SetAttribute('src', $icon_path)
        [void]$binding.AppendChild($imageNode)
    }

    $audioNode = $xml.CreateElement('audio')
    if ($silent -eq 'True') {
        $audioNode.SetAttribute('silent', 'true')
    } else {
        $audioNode.SetAttribute('silent', 'false')
        if ($sound_src) {
            $audioNode.SetAttribute('src', $sound_src)
        }
        if ($sound_loop -eq 'True') {
            $audioNode.SetAttribute('loop', 'true')
        }
    }
    [void]$toastNode.AppendChild($audioNode)

    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    if ($tag) {
        $toast.Tag = $tag
        $toast.Group = 'yyds-notify'
    }

    try {
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($notifier_id).Show($toast)
    } catch {
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app_id).Show($toast)
    }
    exit 0
} catch {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $notification = New-Object System.Windows.Forms.NotifyIcon
        $notification.Icon = [System.Drawing.SystemIcons]::Information
        $tipTitle = if ($title.Length -gt 63) { $title.Substring(0, 63) } else { $title }
        $fullMsg = if ($subtitle) { "$subtitle`n$message" } else { $message }
        $tipText = if ($fullMsg.Length -gt 255) { $fullMsg.Substring(0, 255) } else { $fullMsg }
        $notification.BalloonTipTitle = $tipTitle
        $notification.BalloonTipText = $tipText
        $notification.Visible = $true
        $notification.ShowBalloonTip(3000)
        Start-Sleep -Milliseconds 1500
        $notification.Dispose()
        exit 0
    } catch {
        exit 1
    }
}
""".strip()

_WIN_PS_ENCODED_COMMAND = base64.b64encode(_WIN_PS_SCRIPT.encode("utf-16le")).decode(
    "ascii"
)

_MAC_OSASCRIPT = (
    "on run argv\n"
    "    set theTitle to item 1 of argv\n"
    "    set theMsg to item 2 of argv\n"
    "    set theSub to item 3 of argv\n"
    "    set theSound to item 4 of argv\n"
    "    try\n"
    '        if theSub is not "" then\n'
    '            if theSound is not "" then\n'
    '                tell application "Finder" to display notification theMsg with title theTitle subtitle theSub sound name theSound\n'
    "            else\n"
    '                tell application "Finder" to display notification theMsg with title theTitle subtitle theSub\n'
    "            end if\n"
    "        else\n"
    '            if theSound is not "" then\n'
    '                tell application "Finder" to display notification theMsg with title theTitle sound name theSound\n'
    "            else\n"
    '                tell application "Finder" to display notification theMsg with title theTitle\n'
    "            end if\n"
    "        end if\n"
    "    on error\n"
    '        if theSub is not "" then\n'
    '            if theSound is not "" then\n'
    "                display notification theMsg with title theTitle subtitle theSub sound name theSound\n"
    "            else\n"
    "                display notification theMsg with title theTitle subtitle theSub\n"
    "            end if\n"
    "        else\n"
    '            if theSound is not "" then\n'
    "                display notification theMsg with title theTitle sound name theSound\n"
    "            else\n"
    "                display notification theMsg with title theTitle\n"
    "            end if\n"
    "        end if\n"
    "    end try\n"
    "end run"
)


def _which(binary: str) -> Optional[str]:
    """Looks up an executable on PATH, caching stdlib lookups by current PATH."""
    current_which = shutil.which
    if current_which is not _STDLIB_WHICH:
        return current_which(binary)

    path_env = os.environ.get("PATH", "")
    cache_key = (binary, path_env)
    with _which_cache_lock:
        if cache_key in _which_cache:
            return _which_cache[cache_key]

    resolved = current_which(binary)
    with _which_cache_lock:
        if len(_which_cache) >= 32:
            _which_cache.clear()
        _which_cache[cache_key] = resolved
    return resolved


def _run_command(
    cmd: list,
    *,
    env: Optional[dict] = None,
    timeout: float = 10.0,
    creationflags: int = 0,
    capture_stderr: bool = False,
) -> subprocess.CompletedProcess:
    """
    Executes a platform helper command without allocating unused stdout pipes
    and without hanging indefinitely after a timeout when a grandchild daemon
    inherits stderr.
    """
    current_run = subprocess.run
    if current_run is not _STDLIB_SUBPROCESS_RUN:
        run_kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
            "timeout": timeout,
        }
        if env is not None:
            run_kwargs["env"] = env
        if creationflags:
            run_kwargs["creationflags"] = creationflags
        return current_run(cmd, **run_kwargs)

    popen_kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
    }
    if env is not None:
        popen_kwargs["env"] = env
    if creationflags:
        popen_kwargs["creationflags"] = creationflags

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        _, stderr_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            _, stderr_bytes = proc.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            # On Windows, `communicate` spawns a daemon `stderr_thread` blocked
            # in `BufferedReader.read()`; calling `proc.stderr.close()` from
            # this thread would block on `BufferedReader`'s mutex until any
            # grandchild holding the pipe exits.
            stderr_thread = getattr(proc, "stderr_thread", None)
            if proc.stderr is not None and not (
                stderr_thread is not None and stderr_thread.is_alive()
            ):
                proc.stderr.close()
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            stderr_bytes = b""
        raise subprocess.TimeoutExpired(cmd, timeout, stderr=stderr_bytes)
    except Exception:
        proc.kill()
        stderr_thread = getattr(proc, "stderr_thread", None)
        if proc.stderr is not None and not (
            stderr_thread is not None and stderr_thread.is_alive()
        ):
            proc.stderr.close()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        raise

    return subprocess.CompletedProcess(cmd, proc.returncode, b"", stderr_bytes or b"")


def _emit_fallback(title: str, message: str, subtitle: Optional[str] = None) -> None:
    """Writes a best-effort terminal fallback without raising."""
    fallback_msg = f"\a[Notification] {title}"
    if subtitle:
        fallback_msg += f" ({subtitle})"
    fallback_msg += f": {message}"

    try:
        sys.stderr.write(fallback_msg + "\n")
        sys.stderr.flush()
    except Exception:
        try:
            print(fallback_msg)
        except Exception:
            pass

    logger.info(fallback_msg)


def _queue_full(args: _NotificationArgs) -> bool:
    logger.warning(
        "Notification queue is full (%d pending); notification was not queued.",
        _MAX_PENDING_NOTIFICATIONS,
    )
    if args[-1]:
        _emit_fallback(args[0], args[1], args[2])
    return False


class _NotificationDispatcher:
    """
    Bounded, lazy, fork-aware asynchronous notification dispatcher.

    Uses daemon worker threads coordinated by a single Condition lock so that:
    - importing the package or calling `notify(..., block=True)` starts 0 threads;
    - worker count scales up to `max_workers` on bursts and scales back down to 0
      after `idle_timeout` seconds of inactivity;
    - updates sharing a `replace_id` are strictly serialized and coalesced;
    - interpreter exit runs `atexit` bounded `shutdown(timeout=5.0)` instead of
      hanging indefinitely in `concurrent.futures.thread._python_exit`.
    """

    def __init__(
        self,
        max_workers: int = _MAX_WORKERS,
        max_pending: int = _MAX_PENDING_NOTIFICATIONS,
        idle_timeout: float = _WORKER_IDLE_TIMEOUT,
    ) -> None:
        self._max_workers = max_workers
        self._max_pending = max_pending
        self._idle_timeout = idle_timeout
        self._pid = os.getpid()
        self._cond = threading.Condition(threading.Lock())
        self._queue: deque = deque()
        self._keyed_jobs: dict = {}
        self._pending_count = 0
        self._active_jobs = 0
        self._workers = 0
        self._idle_workers = 0
        self._worker_seq = 0
        self._closed = False

    def _ensure_worker_locked(self) -> bool:
        if self._idle_workers >= len(self._queue) or self._workers >= self._max_workers:
            return True
        self._worker_seq += 1
        worker = threading.Thread(
            target=self._worker_loop,
            name=f"yyds_notify_os_worker_{self._worker_seq}",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            return self._workers > 0
        self._workers += 1
        return True

    @staticmethod
    def _execute_one(args: _NotificationArgs, replace_id: object = None) -> None:
        try:
            result = _send_notification_sync(*args)
        except Exception as error:
            if args[-1]:
                _emit_fallback(args[0], args[1], args[2])
            if replace_id is not None:
                logger.error(
                    "Background notification for replace_id=%r failed: %s",
                    replace_id,
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
            else:
                logger.error(
                    "Background notification failed: %s",
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
            return

        if result is False:
            logger.warning("Background notification could not be delivered.")

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._idle_workers += 1
                    try:
                        notified = self._cond.wait(timeout=self._idle_timeout)
                    finally:
                        self._idle_workers -= 1
                    if not notified and not self._queue:
                        self._workers -= 1
                        if self._workers == 0:
                            self._cond.notify_all()
                        return
                if not self._queue and self._closed:
                    self._workers -= 1
                    self._cond.notify_all()
                    return
                item = self._queue.popleft()

            if item[0] == "unkeyed":
                _, args = item
                try:
                    self._execute_one(args)
                finally:
                    with self._cond:
                        self._pending_count -= 1
                        self._active_jobs -= 1
                        if self._active_jobs == 0:
                            self._cond.notify_all()
            else:
                _, key, state = item
                try:
                    while True:
                        with self._cond:
                            current_args = state["pending"]
                            state["pending"] = None
                        if current_args is not None:
                            self._execute_one(current_args, replace_id=key[1])
                        with self._cond:
                            if state["pending"] is None:
                                if self._keyed_jobs.get(key) is state:
                                    del self._keyed_jobs[key]
                                break
                finally:
                    with self._cond:
                        if self._keyed_jobs.get(key) is state:
                            del self._keyed_jobs[key]
                        self._pending_count -= 1
                        self._active_jobs -= 1
                        if self._active_jobs == 0:
                            self._cond.notify_all()

    def submit(self, args: _NotificationArgs) -> bool:
        replace_id = args[8]
        key = (type(replace_id), replace_id) if replace_id is not None else None

        is_closed = False
        queue_is_full = False
        spawn_failed = False

        with self._cond:
            if self._closed:
                is_closed = True
            elif key is not None and key in self._keyed_jobs:
                # Coalesce into the existing queued or running job for this ID.
                self._keyed_jobs[key]["pending"] = args
                return True
            elif self._pending_count >= self._max_pending:
                queue_is_full = True
            else:
                if key is not None:
                    state = {"pending": args}
                    self._keyed_jobs[key] = state
                    item: tuple = ("keyed", key, state)
                else:
                    item = ("unkeyed", args)

                self._pending_count += 1
                self._active_jobs += 1
                self._queue.append(item)

                if not self._ensure_worker_locked():
                    self._queue.pop()
                    self._pending_count -= 1
                    self._active_jobs -= 1
                    if key is not None:
                        self._keyed_jobs.pop(key, None)
                    spawn_failed = True
                else:
                    self._cond.notify()
                    return True

        if is_closed:
            logger.warning(
                "Notification executor is shut down. Falling back to synchronous delivery."
            )
            return _send_notification_sync(*args)

        if spawn_failed:
            logger.warning(
                "Unable to start background notification worker. "
                "Falling back to synchronous delivery."
            )
            return _send_notification_sync(*args)

        if queue_is_full:
            return _queue_full(args)

        return False  # pragma: no cover

    def shutdown(self, wait: bool = True, timeout: Optional[float] = 5.0) -> None:
        deadline = None if (not wait or timeout is None) else time.monotonic() + timeout
        with self._cond:
            self._closed = True
            if not wait:
                dropped = len(self._queue)
                for item in self._queue:
                    if item[0] == "keyed":
                        _, key, state = item
                        if self._keyed_jobs.get(key) is state:
                            del self._keyed_jobs[key]
                self._queue.clear()
                for state in self._keyed_jobs.values():
                    state["pending"] = None
                self._pending_count -= dropped
                self._active_jobs -= dropped
            self._cond.notify_all()

            if not wait:
                return

            while self._active_jobs > 0 and self._workers > 0:
                if deadline is None:
                    self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)


_dispatcher: Optional[_NotificationDispatcher] = None
_dispatcher_lock = threading.Lock()
_shutdown_requested = False
_state_pid = os.getpid()


def _reset_after_fork_in_child() -> None:
    """Re-initializes locks and drops inherited parent worker state after fork."""
    global _dispatcher, _dispatcher_lock, _shutdown_requested, _state_pid
    global _linux_notify_send_profile_lock, _which_cache_lock, _win_aumid_lock
    _dispatcher_lock = threading.Lock()
    _linux_notify_send_profile_lock = threading.Lock()
    _which_cache_lock = threading.Lock()
    _win_aumid_lock = threading.Lock()
    _dispatcher = None
    _shutdown_requested = False
    _state_pid = os.getpid()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork_in_child)


def _sync_pid_locked() -> None:
    global _dispatcher, _shutdown_requested, _state_pid
    current_pid = os.getpid()
    if _state_pid != current_pid or (
        _dispatcher is not None and _dispatcher._pid != current_pid
    ):
        _dispatcher = None
        _shutdown_requested = False
        _state_pid = current_pid


def _get_dispatcher(create: bool = True) -> Optional[_NotificationDispatcher]:
    global _dispatcher
    with _dispatcher_lock:
        _sync_pid_locked()
        if _dispatcher is None and create and not _shutdown_requested:
            _dispatcher = _NotificationDispatcher()
        return _dispatcher


def shutdown(wait: bool = True, timeout: Optional[float] = 5.0) -> None:
    """
    Shuts down the background notification dispatcher.

    Args:
        wait (bool): If True, wait for pending notifications to complete.
                     If False, discard unstarted queued notifications immediately.
        timeout (float or None): Maximum time in seconds to wait for pending
                                 notifications when `wait` is True. Already-running
                                 system commands are not forcefully terminated when
                                 this timeout expires.
    """
    global _shutdown_requested
    if not isinstance(wait, bool):
        raise ValueError("Shutdown 'wait' must be a boolean.")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout < 0
    ):
        raise ValueError("Shutdown 'timeout' must be a non-negative number or None.")

    with _dispatcher_lock:
        _sync_pid_locked()
        dispatcher = _dispatcher
        _shutdown_requested = True

    if dispatcher is not None:
        dispatcher.shutdown(wait=wait, timeout=timeout)


def _atexit_cleanup() -> None:
    with _dispatcher_lock:
        _sync_pid_locked()
        if _shutdown_requested or _dispatcher is None:
            return
    shutdown(wait=True, timeout=5.0)


atexit.register(_atexit_cleanup)


def _submit_async(args: _NotificationArgs) -> bool:
    dispatcher = _get_dispatcher(create=True)
    if dispatcher is None:
        logger.warning(
            "Notification executor is shut down. Falling back to synchronous delivery."
        )
        return _send_notification_sync(*args)
    return dispatcher.submit(args)


def _sanitize_string(
    text: object, max_len: int, collapse_whitespace: bool = False
) -> str:
    """
    Streams character sanitization so leading control bytes or long runs of
    internal whitespace cannot prematurely truncate valid trailing text while
    still bounding total work on multi-MB inputs.
    """
    if text is None:
        return ""
    raw = text if isinstance(text, str) else str(text)
    if not raw:
        return ""

    raw = raw.strip()
    if not raw:
        return ""

    if len(raw) > _HARD_SCAN_CAP:
        raw = raw[:_HARD_SCAN_CAP]

    out: list = []
    limit = max_len + 1
    pending_space = False
    idx = 0
    length = len(raw)

    while idx < length:
        ch = raw[idx]
        idx += 1
        if ch == "\r":
            if idx < length and raw[idx] == "\n":
                idx += 1
            ch = "\n"
        elif not (ch.isprintable() or ch in "\n\t"):
            continue

        if collapse_whitespace:
            if ch.isspace():
                if out:
                    pending_space = True
                continue
            if pending_space:
                out.append(" ")
                pending_space = False
                if len(out) >= limit:
                    break
            out.append(ch)
            if len(out) >= limit:
                break
        else:
            out.append(ch)
            if len(out) >= limit:
                break

    cleaned = "".join(out).strip()
    if len(cleaned) > max_len:
        suffix = "..." if max_len >= 3 else ""
        prefix = cleaned[: max_len - len(suffix)]
        if collapse_whitespace:
            prefix = prefix.rstrip()
        cleaned = prefix + suffix
    return cleaned


def _resolve_icon_path(icon: Optional[PathLikeStr]) -> Optional[str]:
    """
    Resolves `icon` to an absolute file path when it points to an existing
    local regular file (expanding `~`). Directories are ignored (`None`), and
    freedesktop icon theme names (e.g., 'dialog-information') are kept as-is.
    """
    if icon is None or icon == "":
        return None

    try:
        resolved = os.fspath(icon)
    except TypeError as error:
        raise ValueError(
            "Notification 'icon' must be a string or path-like value."
        ) from error
    if not isinstance(resolved, str):
        raise ValueError("Notification 'icon' must resolve to a text path.")
    if "\x00" in resolved:
        raise ValueError("Notification 'icon' cannot contain null bytes.")

    expanded = os.path.expanduser(resolved)
    if os.path.isabs(expanded):
        if os.path.isdir(expanded):
            return None
        return expanded

    if os.path.isfile(expanded):
        return os.path.abspath(expanded)

    if os.path.exists(expanded):
        # Exists on disk as a directory/non-regular node, not an icon file.
        return None

    if (
        platform.system() in _UNIX_NOTIFY_SYSTEMS
        and "/" not in resolved
        and "\\" not in resolved
    ):
        return resolved

    return resolved


def _normalize_urgency(urgency: str) -> str:
    if not isinstance(urgency, str):
        raise ValueError("Notification 'urgency' must be low, normal, or critical.")
    normalized = urgency.lower().strip()
    if normalized not in ("low", "normal", "critical"):
        raise ValueError("Notification 'urgency' must be low, normal, or critical.")
    return normalized


def _normalize_timeout(timeout: int) -> int:
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ValueError("Notification 'timeout' must be an integer number of seconds.")
    if not 1 <= timeout <= 86400:
        raise ValueError("Notification 'timeout' must be between 1 and 86400 seconds.")
    return timeout


def _normalize_sound(sound: SoundOption) -> SoundOption:
    if isinstance(sound, bool):
        return sound
    if isinstance(sound, str):
        cleaned = _sanitize_string(sound, 64, collapse_whitespace=True)
        if not cleaned:
            return False
        return cleaned
    raise ValueError("Notification 'sound' must be a boolean or sound name string.")


def _normalize_replace_id(
    replace_id: Optional[ReplaceIdOption],
) -> Optional[ReplaceIdOption]:
    if replace_id is None:
        return None
    if isinstance(replace_id, bool):
        raise ValueError("Notification 'replace_id' must be a string or integer.")
    if isinstance(replace_id, int):
        if not 0 <= replace_id <= _UINT32_MAX:
            raise ValueError("Integer 'replace_id' must be between 0 and 4294967295.")
        return replace_id
    if isinstance(replace_id, str):
        cleaned = _sanitize_string(replace_id, 256, collapse_whitespace=True)
        if not cleaned:
            raise ValueError("Notification 'replace_id' cannot be empty.")
        return cleaned
    raise ValueError("Notification 'replace_id' must be a string or integer.")


def _prepare_notification_args(
    title: str,
    message: str,
    subtitle: Optional[str] = None,
    icon: Optional[PathLikeStr] = None,
    urgency: str = "normal",
    timeout: int = 5,
    sound: SoundOption = False,
    app_name: str = "yyds-notify",
    replace_id: Optional[ReplaceIdOption] = None,
    block: bool = False,
    fallback_to_print: bool = True,
) -> _NotificationArgs:
    clean_title = _sanitize_string(title, 128)
    clean_message = _sanitize_string(message, 1024)
    clean_subtitle = (_sanitize_string(subtitle, 128) or None) if subtitle else None
    raw_app_name = (
        _sanitize_string(app_name, 64, collapse_whitespace=True) if app_name else ""
    )
    clean_app_name = raw_app_name or "yyds-notify"

    if not clean_title:
        raise ValueError("Notification 'title' cannot be empty.")
    if not clean_message:
        raise ValueError("Notification 'message' cannot be empty.")
    if not isinstance(block, bool):
        raise ValueError("Notification 'block' must be a boolean.")
    if not isinstance(fallback_to_print, bool):
        raise ValueError("Notification 'fallback_to_print' must be a boolean.")

    clean_icon = _resolve_icon_path(icon)
    clean_urgency = _normalize_urgency(urgency)
    clean_timeout = _normalize_timeout(timeout)
    clean_sound = _normalize_sound(sound)
    clean_replace_id = _normalize_replace_id(replace_id)

    return (
        clean_title,
        clean_message,
        clean_subtitle,
        clean_icon,
        clean_urgency,
        clean_timeout,
        clean_sound,
        clean_app_name,
        clean_replace_id,
        fallback_to_print,
    )


def _stable_replace_number(replace_id: ReplaceIdOption) -> int:
    """
    Maps an arbitrary replace_id into a deterministic positive 31-bit integer
    (`1..2147483647`).

    `notify-send -r` parses signed 32-bit `gint` (`<= 0x7FFFFFFF`), and `0`
    means "do not replace" in the Desktop Notifications specification.
    """
    payload = (
        replace_id.encode("utf-8")
        if isinstance(replace_id, str)
        else f"int:{replace_id}".encode("utf-8")
    )
    digest = hashlib.sha256(payload).hexdigest()
    return (int(digest[:8], 16) & _INT32_MAX) or 1


def _resolve_linux_replace_id(replace_id: ReplaceIdOption) -> int:
    if (
        isinstance(replace_id, int)
        and not isinstance(replace_id, bool)
        and 1 <= replace_id <= _INT32_MAX
    ):
        return replace_id
    return _stable_replace_number(replace_id)


def _resolve_linux_sound(sound: SoundOption) -> str:
    if not sound:
        return ""
    if sound is True:
        return LINUX_SOUND_MAP["default"]
    return LINUX_SOUND_MAP.get(sound.lower(), sound)


def _build_linux_notify_send_command(
    title: str,
    message: str,
    subtitle: Optional[str],
    icon_path: Optional[str],
    urgency: str,
    timeout: int,
    sound: SoundOption,
    app_name: str,
    replace_id: Optional[ReplaceIdOption],
    base_profile: str,
    use_replace: bool,
) -> list:
    """
    Builds one `notify-send` command shape:
    - Escapes XML/Pango entities (`&`, `<`, `>`) in the body so literal `<` or
      `&` cannot swallow text or break notification daemon markup parsing.
    - Terminates options with `--` so leading `-` in `title` or `body` is safe.
    """
    escaped_body = html.escape(message, quote=False)
    if subtitle:
        escaped_sub = html.escape(subtitle, quote=False)
        escaped_body = f"{escaped_sub}\n{escaped_body}"

    if base_profile == "minimal":
        return ["notify-send", "--", title, escaped_body]

    cmd = [
        "notify-send",
        "-t",
        str(timeout * 1000),
        "-u",
        urgency,
    ]
    if base_profile == "with_app" and app_name:
        cmd.extend(["-a", app_name])
    if icon_path:
        cmd.extend(["-i", icon_path])
    sound_name = _resolve_linux_sound(sound)
    if sound_name and base_profile == "with_app":
        cmd.extend(["-h", f"string:sound-name:{sound_name}"])
    if use_replace and replace_id is not None:
        cmd.extend(["-r", str(_resolve_linux_replace_id(replace_id))])
    cmd.extend(["--", title, escaped_body])
    return cmd


def _linux_candidate_specs(
    replace_id: Optional[ReplaceIdOption],
    cached_state: Tuple[Optional[str], Optional[bool]],
) -> Tuple[list, bool]:
    """
    Returns `(specs, single_cached_shot)` where each spec is
    `(base_profile, use_replace, next_state_on_success)`.
    """
    base_profile, replace_supported = cached_state
    needs_replace = replace_id is not None

    if base_profile is None:
        if needs_replace:
            return (
                [
                    ("with_app", True, ("with_app", True)),
                    ("with_app", False, ("with_app", False)),
                    ("without_app", False, ("without_app", False)),
                    ("minimal", False, ("minimal", False)),
                ],
                False,
            )
        return (
            [
                ("with_app", False, ("with_app", None)),
                ("without_app", False, ("without_app", False)),
                ("minimal", False, ("minimal", False)),
            ],
            False,
        )

    if base_profile == "with_app" and replace_supported is None and needs_replace:
        return (
            [
                ("with_app", True, ("with_app", True)),
                ("with_app", False, ("with_app", False)),
            ],
            False,
        )

    use_replace = bool(replace_supported) and needs_replace
    return ([(base_profile, use_replace, cached_state)], True)


def _windows_replace_tag(replace_id: Optional[ReplaceIdOption]) -> str:
    if replace_id is None:
        return ""
    tag = str(replace_id)
    if len(tag) <= 64:
        return tag
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _windows_custom_aumid(app_name: str) -> str:
    if not app_name:
        return ""
    digest = hashlib.sha256(app_name.encode("utf-8")).hexdigest()[:16]
    return f"YYDSNotify.{digest}"


def _escape_terminal_notifier_arg(value: str) -> str:
    """
    `terminal-notifier` parses option values via `NSUserDefaults` property lists
    and strips a single leading `\\` via `substringFromIndex:1` in `AppDelegate.m`.
    """
    if value and value[0] in _TERMINAL_NOTIFIER_ESCAPE_PREFIXES:
        return "\\" + value
    return value


def _resolve_macos_sound(sound: SoundOption, for_terminal_notifier: bool) -> str:
    if not sound:
        return ""
    if sound is True:
        return "default" if for_terminal_notifier else "Tink"
    lowered = sound.lower()
    if lowered == "default":
        return "default" if for_terminal_notifier else "Tink"
    return MAC_SOUND_MAP.get(lowered, sound)


def _send_notification_sync(
    title: str,
    message: str,
    subtitle: Optional[str] = None,
    icon: Optional[PathLikeStr] = None,
    urgency: str = "normal",
    timeout: int = 5,
    sound: SoundOption = False,
    app_name: str = "yyds-notify",
    replace_id: Optional[ReplaceIdOption] = None,
    fallback_to_print: bool = True,
) -> bool:
    """
    Synchronously triggers a system notification on the current platform.
    Returns True if GUI notification succeeded, False if it failed or fell back.
    """
    global _linux_notify_send_state

    system = platform.system()
    debug_enabled = logger.isEnabledFor(logging.DEBUG)

    # Defensive normalization for direct internal callers.
    urgency = urgency.lower().strip() if isinstance(urgency, str) else "normal"
    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"

    icon_path = _resolve_icon_path(icon)
    success = False

    # ----------------------------------------------------
    # WINDOWS IMPLEMENTATION
    # ----------------------------------------------------
    if system == "Windows":
        sound_src = ""
        sound_loop = "False"
        silent = "True"
        if sound:
            silent = "False"
            if isinstance(sound, str) and sound.lower() in WIN_SOUND_MAP:
                key = sound.lower()
                sound_src = WIN_SOUND_MAP[key]
                if key in _WIN_LOOPING_SOUNDS:
                    sound_loop = "True"
            else:
                sound_src = WIN_SOUND_MAP["default"]

        duration = (
            "long"
            if (urgency == "critical" or timeout >= 20 or sound_loop == "True")
            else ""
        )
        win_icon = icon_path if (icon_path and os.path.isfile(icon_path)) else ""
        custom_aumid = _windows_custom_aumid(app_name)
        sync_reg = "False"
        if custom_aumid and app_name:
            with _win_aumid_lock:
                if _win_aumid_cache.get(custom_aumid) != (app_name, win_icon):
                    sync_reg = "True"

        env = os.environ.copy()
        env["NOTIFY_TITLE"] = title
        env["NOTIFY_SUBTITLE"] = subtitle or ""
        env["NOTIFY_MESSAGE"] = message
        env["NOTIFY_APP_NAME"] = app_name
        env["NOTIFY_CUSTOM_AUMID"] = custom_aumid
        env["NOTIFY_SYNC_REG"] = sync_reg
        env["NOTIFY_APP_ID"] = _WIN_POWERSHELL_APP_ID
        env["NOTIFY_ICON_PATH"] = win_icon
        env["NOTIFY_SOUND_SRC"] = sound_src
        env["NOTIFY_SOUND_LOOP"] = sound_loop
        env["NOTIFY_SILENT"] = silent
        env["NOTIFY_DURATION"] = duration
        env["NOTIFY_TAG"] = _windows_replace_tag(replace_id)

        try:
            res = _run_command(
                [
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-EncodedCommand",
                    _WIN_PS_ENCODED_COMMAND,
                ],
                env=env,
                timeout=10,
                creationflags=0x08000000
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
                capture_stderr=debug_enabled,
            )
            success = res.returncode == 0
            if success and sync_reg == "True":
                with _win_aumid_lock:
                    if len(_win_aumid_cache) >= 64:
                        _win_aumid_cache.clear()
                    _win_aumid_cache[custom_aumid] = (app_name, win_icon)
        except subprocess.TimeoutExpired as error:
            logger.warning("PowerShell notification timed out: %s", error)
            success = False
        except Exception as error:
            logger.debug("PowerShell notification failed: %s", error)
            success = False

    # ----------------------------------------------------
    # MACOS IMPLEMENTATION
    # ----------------------------------------------------
    elif system == "Darwin":
        if _which("terminal-notifier") is not None:
            cmd = [
                "terminal-notifier",
                "-title",
                _escape_terminal_notifier_arg(title),
                "-message",
                _escape_terminal_notifier_arg(message),
            ]
            if subtitle:
                cmd.extend(["-subtitle", _escape_terminal_notifier_arg(subtitle)])
            if replace_id is not None:
                cmd.extend(["-group", _escape_terminal_notifier_arg(str(replace_id))])
            if icon_path and os.path.isfile(icon_path):
                cmd.extend(["-appIcon", icon_path])
            tn_sound = _resolve_macos_sound(sound, for_terminal_notifier=True)
            if tn_sound:
                cmd.extend(["-sound", tn_sound])

            try:
                res = _run_command(cmd, timeout=10, capture_stderr=debug_enabled)
                if res.returncode == 0:
                    return True
                logger.debug(
                    "terminal-notifier failed with return code %d. Stderr: %s",
                    res.returncode,
                    res.stderr.decode("utf-8", errors="ignore") if res.stderr else "",
                )
            except subprocess.TimeoutExpired as error:
                logger.warning("terminal-notifier timed out: %s", error)
            except Exception as error:
                logger.debug("terminal-notifier execution failed: %s", error)

        osa_sound = _resolve_macos_sound(sound, for_terminal_notifier=False)
        try:
            res = _run_command(
                [
                    "osascript",
                    "-e",
                    _MAC_OSASCRIPT,
                    "--",
                    title,
                    message,
                    subtitle or "",
                    osa_sound,
                ],
                timeout=10,
                capture_stderr=debug_enabled,
            )
            success = res.returncode == 0
            if not success:
                logger.debug(
                    "AppleScript notification failed with return code %d. Stderr: %s",
                    res.returncode,
                    res.stderr.decode("utf-8", errors="ignore") if res.stderr else "",
                )
        except subprocess.TimeoutExpired as error:
            logger.warning("AppleScript notification timed out: %s", error)
            success = False
        except Exception as error:
            logger.debug("AppleScript notification failed: %s", error)
            success = False

    # ----------------------------------------------------
    # LINUX & BSD UNIX IMPLEMENTATION
    # ----------------------------------------------------
    elif system in _UNIX_NOTIFY_SYSTEMS:
        has_display = bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )
        has_dbus_or_display = has_display or bool(
            os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        )
        usable_unix_icon = (
            icon_path
            if (
                icon_path
                and (
                    ("/" not in icon_path and "\\" not in icon_path)
                    or os.path.isfile(icon_path)
                )
            )
            else None
        )

        if has_dbus_or_display:
            if _which("notify-send") is not None:
                with _linux_notify_send_profile_lock:
                    cached_state = _linux_notify_send_state

                specs, single_cached_shot = _linux_candidate_specs(
                    replace_id, cached_state
                )
                timed_out = False

                for base_prof, use_replace, next_state in specs:
                    current_cmd = _build_linux_notify_send_command(
                        title,
                        message,
                        subtitle,
                        usable_unix_icon,
                        urgency,
                        timeout,
                        sound,
                        app_name,
                        replace_id,
                        base_prof,
                        use_replace,
                    )
                    try:
                        res = _run_command(
                            current_cmd, timeout=10, capture_stderr=debug_enabled
                        )
                        if res.returncode == 0:
                            success = True
                            with _linux_notify_send_profile_lock:
                                _linux_notify_send_state = next_state
                            break
                        logger.debug(
                            "notify-send (%s, replace=%s) returned %d. Stderr: %s",
                            base_prof,
                            use_replace,
                            res.returncode,
                            res.stderr.decode("utf-8", errors="ignore")
                            if res.stderr
                            else "",
                        )
                    except subprocess.TimeoutExpired as error:
                        logger.warning(
                            "Command %s timed out: %s", " ".join(current_cmd), error
                        )
                        timed_out = True
                        break
                    except Exception as error:
                        logger.debug(
                            "Command %s failed: %s", " ".join(current_cmd), error
                        )

                if not success and (single_cached_shot or cached_state != (None, None)):
                    with _linux_notify_send_profile_lock:
                        if _linux_notify_send_state == cached_state and not timed_out:
                            _linux_notify_send_state = (None, None)

            # Zenity requires an X11 or Wayland display to initialize GTK.
            if not success and has_display and _which("zenity") is not None:
                escaped_title = html.escape(title)
                escaped_msg = html.escape(message)
                zenity_text = f"<b>{escaped_title}</b>\n{escaped_msg}"
                if subtitle:
                    escaped_sub = html.escape(subtitle)
                    zenity_text = (
                        f"<b>{escaped_title}</b>\n<i>{escaped_sub}</i>\n{escaped_msg}"
                    )

                zenity_cmds = []
                if usable_unix_icon:
                    # Zenity 4+ uses `--icon=`; older Zenity 3.x used `--window-icon=`.
                    zenity_cmds.append(
                        [
                            "zenity",
                            "--notification",
                            f"--text={zenity_text}",
                            f"--icon={usable_unix_icon}",
                        ]
                    )
                    zenity_cmds.append(
                        [
                            "zenity",
                            "--notification",
                            f"--text={zenity_text}",
                            f"--window-icon={usable_unix_icon}",
                        ]
                    )
                else:
                    zenity_cmds.append(
                        ["zenity", "--notification", f"--text={zenity_text}"]
                    )

                for z_cmd in zenity_cmds:
                    try:
                        res = _run_command(
                            z_cmd, timeout=10, capture_stderr=debug_enabled
                        )
                        if res.returncode == 0:
                            success = True
                            break
                    except subprocess.TimeoutExpired as error:
                        logger.warning("zenity notification timed out: %s", error)
                        break
                    except Exception as error:
                        logger.debug("zenity command failed: %s", error)
                        break

        else:
            logger.debug(
                "System is running in headless mode. GUI notifications skipped."
            )
            success = False

    else:
        logger.debug(
            "Unsupported operating system %r. GUI notifications skipped.", system
        )
        success = False

    # ----------------------------------------------------
    # DEGRADED FALLBACK
    # ----------------------------------------------------
    if not success and fallback_to_print:
        _emit_fallback(title, message, subtitle)
        return False

    return success


def notify(
    title: str,
    message: str,
    subtitle: Optional[str] = None,
    icon: Optional[PathLikeStr] = None,
    urgency: str = "normal",
    timeout: int = 5,
    sound: SoundOption = False,
    app_name: str = "yyds-notify",
    replace_id: Optional[ReplaceIdOption] = None,
    block: bool = False,
    fallback_to_print: bool = True,
) -> bool:
    """
    Sends a cross-platform desktop notification.

    Args:
        title (str): Title of the notification.
        message (str): Body text of the notification.
        subtitle (str, optional): Subtitle of the notification (Windows/macOS/Linux).
        icon (str or os.PathLike, optional): Icon name or local image file path.
        urgency (str): Urgency level: 'low', 'normal', 'critical' (Linux/Windows).
        timeout (int): Expiration timeout in seconds (1..86400). Defaults to 5.
        sound (bool or str): If True, plays default system sound.
                             Supports cross-platform presets ('default', 'im', 'mail',
                             'reminder', 'sms', 'alarm', 'call') and platform sound names.
        app_name (str): Sender application name (Linux/Windows). Defaults to 'yyds-notify'.
        replace_id (str or int, optional): Unique ID for updating an existing popup.
                                           Pending async updates sharing an ID are
                                           serialized and coalesced to the latest value.
        block (bool): If True, wait until the notification command finishes and return
                      its delivery status. If False (default), dispatch asynchronously.
        fallback_to_print (bool): If True (default), write a terminal fallback with an
                                  ASCII bell to `sys.stderr` when GUI delivery fails.

    Returns:
        bool: True if synchronous delivery succeeded or the asynchronous task was accepted;
              False if delivery failed or the bounded queue rejected the task.
    """
    args = _prepare_notification_args(
        title=title,
        message=message,
        subtitle=subtitle,
        icon=icon,
        urgency=urgency,
        timeout=timeout,
        sound=sound,
        app_name=app_name,
        replace_id=replace_id,
        block=block,
        fallback_to_print=fallback_to_print,
    )
    if block:
        return _send_notification_sync(*args)
    return _submit_async(args)


# Convenience aliases
show = notify
send = notify
