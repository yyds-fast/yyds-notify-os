# -*- coding:utf-8 -*-

import os
import sys
import platform
import subprocess
import threading
import logging
import base64
import html
import atexit
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("yyds_notify_os")
logger.addHandler(logging.NullHandler())

# Bounded thread pool for async execution
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yyds_notify_os_worker")
_active_futures = set()
_futures_lock = threading.Lock()


def _add_future(future):
    with _futures_lock:
        _active_futures.add(future)
        future.add_done_callback(lambda f: _active_futures.discard(f))


def shutdown(wait: bool = True, timeout: float = 5.0) -> None:
    """
    Shuts down the background notification executor.

    Args:
        wait (bool): If True, wait for pending notifications to complete.
        timeout (float): Maximum time in seconds to wait for pending notifications.
                         Only used if wait is True.
    """
    _executor.shutdown(wait=False)
    if wait:
        with _futures_lock:
            futures = list(_active_futures)
        if futures:
            from concurrent.futures import wait as wait_futures
            wait_futures(futures, timeout=timeout)


def _atexit_cleanup():
    shutdown(wait=True, timeout=5.0)


atexit.register(_atexit_cleanup)


def _sanitize_string(text: str, max_len: int) -> str:
    """
    Sanitizes string by removing null bytes, cleaning control characters,
    and truncating to max_len.
    """
    if not text:
        return ""
    text = str(text).replace("\x00", "")
    # Keep printable/standard chars, strip raw control chars (except \n, \r, \t)
    text = "".join(c for c in text if c >= ' ' or c in '\n\r\t')
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


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


def _escape_applescript(text: str) -> str:
    """Escapes string inputs for use in AppleScript double quotes."""
    if not text:
        return ""
    return text.replace('\\', '\\\\').replace('"', '\\"')


def _resolve_icon_path(icon: str) -> str:
    """
    Resolves icon path to an absolute path if it points to a local file.
    If it is a Linux system icon name, it is returned as-is.
    """
    if not icon:
        return None
        
    # Check if local file exists
    if os.path.exists(icon):
        return os.path.abspath(icon)
        
    # For Linux, standard icon names are valid (e.g. "dialog-information")
    if platform.system() == "Linux" and "/" not in icon and "\\" not in icon:
        return icon
        
    return icon


def _send_notification_sync(
    title: str,
    message: str,
    subtitle: str = None,
    icon: str = None,
    urgency: str = "normal",
    timeout: int = 5,
    sound = False,
    app_name: str = "yyds-notify",
    replace_id = None,
    fallback_to_print: bool = True,
) -> bool:
    """
    Synchronously triggers a system notification based on the current platform.
    Returns True if notification succeeded, False if it failed/fell back.
    """
    system = platform.system()

    # Validate urgency
    urgency = urgency.lower()
    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"

    # Resolve icon path
    icon_path = _resolve_icon_path(icon)

    success = False

    # ----------------------------------------------------
    # WINDOWS IMPLEMENTATION
    # ----------------------------------------------------
    if system == "Windows":
        # We run PowerShell using environment variables to transfer strings safely
        # and Base64 EncodedCommand to bypass any complex console escaping problems.
        app_id = "{1AC14E77-C8E7-45C6-87F6-C512F4342091}\\WindowsPowerShell\\v1.0\\powershell.exe"
        
        # Map sound to Windows sound events
        sound_src = ""
        silent = "True"
        if sound:
            silent = "False"
            if isinstance(sound, str) and sound.lower() in WIN_SOUND_MAP:
                sound_src = WIN_SOUND_MAP[sound.lower()]
            else:
                sound_src = WIN_SOUND_MAP["default"]

        ps_script = """
        $title = $env:NOTIFY_TITLE
        $message = $env:NOTIFY_MESSAGE
        $app_name = $env:NOTIFY_APP_NAME
        $app_id = $env:NOTIFY_APP_ID
        $icon_path = $env:NOTIFY_ICON_PATH
        $sound_src = $env:NOTIFY_SOUND_SRC
        $silent = $env:NOTIFY_SILENT
        $tag = $env:NOTIFY_TAG

        try {
            [void][System.Reflection.Assembly]::LoadWithPartialName('Windows.UI.Notifications')
            [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
            
            # Use ToastGeneric template for rich visual capabilities (supports custom icons)
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastGeneric)
            $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
            $xml.LoadXml($template.GetXml())
            
            # Set Title & Message
            $texts = $xml.GetElementsByTagName("text")
            if ($texts.Count -gt 0) { $texts.Item(0).AppendChild($xml.CreateTextNode($title)) > $null }
            if ($texts.Count -gt 1) { $texts.Item(1).AppendChild($xml.CreateTextNode($message)) > $null }
            
            # Set Icon (appLogoOverride)
            if ($icon_path) {
                $binding = $xml.GetElementsByTagName("binding").Item(0)
                $imageNode = $xml.CreateElement("image")
                $imageNode.SetAttribute("placement", "appLogoOverride")
                $imageNode.SetAttribute("src", $icon_path)
                $binding.AppendChild($imageNode) > $null
            }
            
            # Configure Sound
            $toastNode = $xml.GetElementsByTagName("toast").Item(0)
            $audioNode = $xml.CreateElement("audio")
            if ($silent -eq "True") {
                $audioNode.SetAttribute("silent", "true")
            } else {
                $audioNode.SetAttribute("silent", "false")
                if ($sound_src) {
                    $audioNode.SetAttribute("src", $sound_src)
                }
            }
            $toastNode.AppendChild($audioNode) > $null
            
            # Instantiate Toast
            $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
            
            # Set Tag for replacing/updating notifications
            if ($tag) {
                $toast.Tag = $tag
            }
            
            # Try to show notification using custom app name first
            try {
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app_name).Show($toast)
            } catch {
                # Fallback to the registered PowerShell App ID if the custom name causes issues
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app_id).Show($toast)
            }
            exit 0
        } catch {
            # Fallback to legacy System.Windows.Forms.NotifyIcon
            try {
                [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
                $notification = New-Object System.Windows.Forms.NotifyIcon
                $notification.Icon = [System.Drawing.SystemIcons]::Information
                $notification.BalloonTipTitle = $title
                $notification.BalloonTipText = $message
                $notification.Visible = $True
                $notification.ShowBalloonTip(5000)
                Start-Sleep -Seconds 5
                $notification.Dispose()
                exit 0
            } catch {
                exit 1
            }
        }
        """
        # Prepare environment variables
        env = os.environ.copy()
        env["NOTIFY_TITLE"] = title
        env["NOTIFY_MESSAGE"] = message
        env["NOTIFY_APP_NAME"] = app_name
        env["NOTIFY_APP_ID"] = app_id
        env["NOTIFY_ICON_PATH"] = icon_path if icon_path else ""
        env["NOTIFY_SOUND_SRC"] = sound_src
        env["NOTIFY_SILENT"] = silent
        env["NOTIFY_TAG"] = str(replace_id) if replace_id is not None else ""

        # Encode script in UTF-16LE and Base64 as PowerShell requires
        encoded_script = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
                env=env,
                capture_output=True,
                timeout=10,
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0, # CREATE_NO_WINDOW
            )
            success = (res.returncode == 0)
        except subprocess.TimeoutExpired as e:
            logger.warning(f"PowerShell notification timed out: {e}")
            success = False
        except Exception as e:
            logger.debug(f"PowerShell notification failed: {e}")
            success = False

    # ----------------------------------------------------
    # MACOS IMPLEMENTATION
    # ----------------------------------------------------
    elif system == "Darwin":
        # 1. Try terminal-notifier first if available (more reliable on modern macOS)
        has_terminal_notifier = False
        try:
            subprocess.run(["which", "terminal-notifier"], capture_output=True, check=True, timeout=5)
            has_terminal_notifier = True
        except Exception:
            pass

        if has_terminal_notifier:
            cmd = ["terminal-notifier", "-title", title, "-message", message]
            if subtitle:
                cmd.extend(["-subtitle", subtitle])
            if sound:
                sound_name = sound if isinstance(sound, str) else "default"
                cmd.extend(["-sound", sound_name])
            
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=10)
                if res.returncode == 0:
                    return True
                else:
                    logger.debug(f"terminal-notifier failed with return code {res.returncode}. Stderr: {res.stderr.decode('utf-8', errors='ignore')}")
            except subprocess.TimeoutExpired as e:
                logger.warning(f"terminal-notifier timed out: {e}")
            except Exception as e:
                logger.debug(f"terminal-notifier execution failed: {e}")

        # 2. Fallback to AppleScript execution using environment variables (system attribute)
        env = os.environ.copy()
        env["NOTIFY_TITLE"] = title
        env["NOTIFY_MESSAGE"] = message
        env["NOTIFY_SUBTITLE"] = subtitle if subtitle else ""
        env["NOTIFY_SOUND"] = sound if isinstance(sound, str) else ("Tink" if sound else "")

        script = (
            'set theTitle to system attribute "NOTIFY_TITLE"\n'
            'set theMsg to system attribute "NOTIFY_MESSAGE"\n'
            'set theSub to system attribute "NOTIFY_SUBTITLE"\n'
            'set theSound to system attribute "NOTIFY_SOUND"\n'
            'try\n'
            '    if theSub is not "" then\n'
            '        if theSound is not "" then\n'
            '            tell application "Finder" to display notification theMsg with title theTitle subtitle theSub sound name theSound\n'
            '        else\n'
            '            tell application "Finder" to display notification theMsg with title theTitle subtitle theSub\n'
            '        end if\n'
            '    else\n'
            '        if theSound is not "" then\n'
            '            tell application "Finder" to display notification theMsg with title theTitle sound name theSound\n'
            '        else\n'
            '            tell application "Finder" to display notification theMsg with title theTitle\n'
            '        end if\n'
            '    end if\n'
            'on error\n'
            '    if theSub is not "" then\n'
            '        if theSound is not "" then\n'
            '            display notification theMsg with title theTitle subtitle theSub sound name theSound\n'
            '        else\n'
            '            display notification theMsg with title theTitle subtitle theSub\n'
            '        end if\n'
            '    else\n'
            '        if theSound is not "" then\n'
            '            display notification theMsg with title theTitle sound name theSound\n'
            '        else\n'
            '            display notification theMsg with title theTitle\n'
            '        end if\n'
            '    end if\n'
            'end try'
        )
            
        try:
            res = subprocess.run(["osascript", "-e", script], env=env, capture_output=True, timeout=10)
            success = (res.returncode == 0)
            if not success:
                logger.debug(f"AppleScript notification failed with return code {res.returncode}. Stderr: {res.stderr.decode('utf-8', errors='ignore')}")
        except subprocess.TimeoutExpired as e:
            logger.warning(f"AppleScript notification timed out: {e}")
            success = False
        except Exception as e:
            logger.debug(f"AppleScript notification failed: {e}")
            success = False

    # ----------------------------------------------------
    # LINUX & OTHER UNIX IMPLEMENTATION
    # ----------------------------------------------------
    else:
        # Detect headless (no DISPLAY or WAYLAND_DISPLAY environment variables)
        is_headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        if not is_headless:
            # Check availability of notify-send
            has_notify_send = False
            try:
                subprocess.run(["which", "notify-send"], capture_output=True, check=True, timeout=5)
                has_notify_send = True
            except Exception:
                pass

            if has_notify_send:
                # Progressive fallbacks for notify-send command to maximize compatibility
                cmds_to_try = []
                
                # 1. Full command (with replace_id, app_name, icon, timeout, urgency)
                cmd_full = ["notify-send", title, message, "-t", str(timeout * 1000), "-u", urgency]
                if app_name:
                    cmd_full.extend(["-a", app_name])
                if icon_path:
                    cmd_full.extend(["-i", icon_path])
                if replace_id is not None:
                    try:
                        resolved_id = int(replace_id)
                    except (ValueError, TypeError):
                        import hashlib
                        h = hashlib.md5(str(replace_id).encode("utf-8")).hexdigest()
                        resolved_id = int(h[:7], 16) + 1
                    cmd_full.extend(["-r", str(resolved_id)])
                cmds_to_try.append(cmd_full)
                
                # 2. Try without replace_id (-r option is not supported on older versions)
                if replace_id is not None:
                    cmd_no_r = ["notify-send", title, message, "-t", str(timeout * 1000), "-u", urgency]
                    if app_name:
                        cmd_no_r.extend(["-a", app_name])
                    if icon_path:
                        cmd_no_r.extend(["-i", icon_path])
                    cmds_to_try.append(cmd_no_r)
                    
                # 3. Try without app_name (-a option might not be supported in some environments)
                cmd_no_a = ["notify-send", title, message, "-t", str(timeout * 1000), "-u", urgency]
                if icon_path:
                    cmd_no_a.extend(["-i", icon_path])
                cmds_to_try.append(cmd_no_a)
                
                # 4. Minimal command (just title and message)
                cmd_minimal = ["notify-send", title, message]
                cmds_to_try.append(cmd_minimal)
                
                # Execute in sequence until one succeeds
                for current_cmd in cmds_to_try:
                    try:
                        res = subprocess.run(current_cmd, capture_output=True, timeout=10)
                        if res.returncode == 0:
                            success = True
                            break
                    except subprocess.TimeoutExpired as e:
                        logger.warning(f"Command {' '.join(current_cmd)} timed out: {e}")
                    except Exception as e:
                        logger.debug(f"Command {' '.join(current_cmd)} failed: {e}")

            # Try zenity fallback if notify-send failed or wasn't available
            if not success:
                try:
                    subprocess.run(["which", "zenity"], capture_output=True, check=True, timeout=5)
                    
                    escaped_title = html.escape(title)
                    escaped_msg = html.escape(message)
                    zenity_text = f"<b>{escaped_title}</b>\n{escaped_msg}"
                    if subtitle:
                        escaped_sub = html.escape(subtitle)
                        zenity_text = f"<b>{escaped_title}</b>\n<i>{escaped_sub}</i>\n{escaped_msg}"
                    
                    cmd = ["zenity", "--notification", f"--text={zenity_text}"]
                    if icon_path:
                        cmd.append(f"--window-icon={icon_path}")
                    
                    res = subprocess.run(cmd, capture_output=True, timeout=10)
                    success = (res.returncode == 0)
                except subprocess.TimeoutExpired as e:
                    logger.warning(f"zenity notification timed out: {e}")
                    success = False
                except Exception as e:
                    logger.debug(f"zenity command failed: {e}")
                    success = False

        else:
            logger.debug("System is running in headless mode. GUI notifications skipped.")
            success = False

    # ----------------------------------------------------
    # DEGRADED FALLBACK
    # ----------------------------------------------------
    if not success and fallback_to_print:
        # Fallback print to terminal or stderr
        fallback_msg = f"\a[Notification] {title}"
        if subtitle:
            fallback_msg += f" ({subtitle})"
        fallback_msg += f": {message}"
        
        try:
            try:
                sys.stderr.write(fallback_msg + "\n")
                sys.stderr.flush()
            except Exception:
                try:
                    print(fallback_msg)
                except Exception:
                    pass
        except Exception:
            pass
            
        logger.info(fallback_msg)
        return False

    return success


def notify(
    title: str,
    message: str,
    subtitle: str = None,
    icon: str = None,
    urgency: str = "normal",
    timeout: int = 5,
    sound = False,
    app_name: str = "yyds-notify",
    replace_id = None,
    block: bool = False,
    fallback_to_print: bool = True,
) -> bool:
    """
    Sends a cross-platform desktop notification.

    Args:
        title (str): Title of the notification.
        message (str): Body text of the notification.
        subtitle (str, optional): Subtitle of the notification (macOS/zenity fallback support).
        icon (str, optional): Icon name or path (Linux/Windows/zenity support).
        urgency (str): Urgency level: 'low', 'normal', 'critical' (Linux support).
        timeout (int): Expiration timeout in seconds (Linux support). Defaults to 5.
        sound (bool or str): If True, plays default system sound (macOS/Windows). 
                             On macOS, can also be a string name of the system sound.
                             On Windows, can be: 'default', 'im', 'mail', 'reminder', 'sms', 'alarm', 'call'.
        app_name (str): The application name triggering the notification (Linux/Windows support).
        replace_id (str or int, optional): A unique ID. If specified, sending a new notification 
                                           with the same ID will replace/update the existing one.
        block (bool): If True, block calling thread until notification command finishes.
                      If False (default), dispatch asynchronously on a daemon thread.
        fallback_to_print (bool): If True (default), prints notification details to terminal 
                                  when OS notification fails or under headless environment.

    Returns:
        bool: True if synchronous invocation succeeded or background thread started, 
              False otherwise.
    """
    # Sanitize and truncate inputs
    clean_title = _sanitize_string(title, 128)
    clean_message = _sanitize_string(message, 1024)
    clean_subtitle = _sanitize_string(subtitle, 128) if subtitle else None
    clean_app_name = _sanitize_string(app_name, 64) if app_name else "yyds-notify"

    if not clean_title:
        raise ValueError("Notification 'title' cannot be empty.")
    if not clean_message:
        raise ValueError("Notification 'message' cannot be empty.")

    args = (
        clean_title,
        clean_message,
        clean_subtitle,
        icon,
        urgency,
        timeout,
        sound,
        clean_app_name,
        replace_id,
        fallback_to_print,
    )

    if block:
        return _send_notification_sync(*args)
    else:
        try:
            future = _executor.submit(_send_notification_sync, *args)
            _add_future(future)
            return True
        except RuntimeError:
            # Executor is shut down (e.g. during application exit), fallback to sync
            logger.warning("Notification executor is shut down. Falling back to synchronous delivery.")
            return _send_notification_sync(*args)


# Convenience aliases
show = notify
send = notify
