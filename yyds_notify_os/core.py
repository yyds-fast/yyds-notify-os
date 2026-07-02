# -*- coding:utf-8 -*-

import os
import sys
import platform
import subprocess
import threading
import logging
import base64

logger = logging.getLogger("yyds_notify_os")

# Setup default logger formatting to be simple and clean
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)


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
        app_id = f"{{{os.environ.get('COMPUTERNAME', 'Python-Notify')}}}\\WindowsPowerShell\\v1.0\\powershell.exe"
        
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
                Start-Sleep -Seconds 1
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
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0, # CREATE_NO_WINDOW
            )
            success = (res.returncode == 0)
        except Exception as e:
            logger.debug(f"PowerShell notification failed: {e}")
            success = False

    # ----------------------------------------------------
    # MACOS IMPLEMENTATION
    # ----------------------------------------------------
    elif system == "Darwin":
        # AppleScript execution
        esc_title = _escape_applescript(title)
        esc_msg = _escape_applescript(message)
        esc_sub = _escape_applescript(subtitle) if subtitle else None
        
        script = f'display notification "{esc_msg}" with title "{esc_title}"'
        if esc_sub:
            script += f' subtitle "{esc_sub}"'
        
        if sound:
            sound_name = sound if isinstance(sound, str) else "Tink"
            script += f' sound name "{sound_name}"'
            
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True)
            success = (res.returncode == 0)
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
                subprocess.run(["which", "notify-send"], capture_output=True, check=True)
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
                    cmd_full.extend(["-r", str(replace_id)])
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
                        res = subprocess.run(current_cmd, capture_output=True)
                        if res.returncode == 0:
                            success = True
                            break
                    except Exception as e:
                        logger.debug(f"Command {' '.join(current_cmd)} failed: {e}")

            # Try zenity fallback if notify-send failed or wasn't available
            if not success:
                try:
                    subprocess.run(["which", "zenity"], capture_output=True, check=True)
                    
                    zenity_text = f"<b>{title}</b>\n{message}"
                    if subtitle:
                        zenity_text = f"<b>{title}</b>\n<i>{subtitle}</i>\n{message}"
                    
                    cmd = ["zenity", "--notification", f"--text={zenity_text}"]
                    if icon_path:
                        cmd.append(f"--window-icon={icon_path}")
                    
                    res = subprocess.run(cmd, capture_output=True)
                    success = (res.returncode == 0)
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
        fallback_msg = f"[Notification] {title}"
        if subtitle:
            fallback_msg += f" ({subtitle})"
        fallback_msg += f": {message}"
        
        try:
            sys.stderr.write(fallback_msg + "\n")
            sys.stderr.flush()
        except Exception:
            print(fallback_msg)
            
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
    if not title:
        raise ValueError("Notification 'title' cannot be empty.")
    if not message:
        raise ValueError("Notification 'message' cannot be empty.")

    args = (title, message, subtitle, icon, urgency, timeout, sound, app_name, replace_id, fallback_to_print)

    if block:
        return _send_notification_sync(*args)
    else:
        # Launch asynchronously in a daemon thread so it is completely non-blocking
        thread = threading.Thread(
            target=lambda: _send_notification_sync(*args),
            daemon=True
        )
        thread.start()
        return True


# Convenience aliases
show = notify
send = notify
