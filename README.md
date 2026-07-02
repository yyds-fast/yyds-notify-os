# yyds-notify-os

A high-performance, lightweight, and easy-to-use cross-platform desktop notification library for Python.

[中文说明 (Chinese README)](README_CN.md)

---

## 💡 Key Features

* **Zero External Dependencies**: Implemented strictly using the Python standard library. It interacts with native OS notification tools via subprocesses.
* **Microsecond-Level Startup**: Highly optimized imports (under 1ms), leaving virtually zero performance footprint.
* **Non-blocking by Default**: Notifications are dispatched asynchronously in background daemon threads, keeping your application GUI or CLI completely lag-free.
* **Notification Replacement/Updating (`replace_id`)**: Update an existing notification card in-place (ideal for progress bars or dynamic alerts) without cluttering the Action Center.
* **Modern Windows Customizations**: Uses Microsoft's modern `ToastGeneric` template to support custom application icons (`icon`) and mapped system sounds (`sound`) or mute configurations.
* **Path Auto-Resolution**: Automatically converts relative icon paths to absolute paths to prevent subprocess path resolution failures.
* **Robust Fail-Safe Fallbacks**: Under headless environments (e.g. SSH sessions) or when graphical notifications fail, it gracefully falls back to console stderr output without crashing, and includes an ASCII bell character (`\a`) to trigger a terminal beep for immediate feedback.
* **Command Line Interface (CLI)**: Out-of-the-box `yyds-notify` / `yyds-notify-os` commands for shell script integrations.

---

## 🚀 Installation

```bash
pip install -U yyds-notify-os
```

Or install from source in editable mode for local development:
```bash
pip install -e .
```

---

## 📂 Examples

You can find runnable usage examples in the [example/](file:///home/wzb/yyds_github/yyds-notify-os/example) directory:
* [demo.py](file:///home/wzb/yyds_github/yyds-notify-os/example/demo.py): Comprehensive Python API examples demonstrating basic usage, custom settings, and dynamic progress bar notifications using `replace_id`.
* [demo.sh](file:///home/wzb/yyds_github/yyds-notify-os/example/demo.sh): Shell script demonstrating command line interface (CLI) usage with various parameters.

---

## 💻 Python API Usage

```python
import yyds_notify_os as notify
import time

# 1. Simple Usage (Non-blocking by default)
notify.send("Task Complete", "Your compilation has finished successfully!")

# 2. Dynamic Update/Replacement (Same ID updates the same card in-place)
for i in range(1, 6):
    notify.send("Downloading", f"Progress: {i*20}%", replace_id="download_task_1")
    time.sleep(1)

# 3. Synchronous Blocking Call (Returns True/False based on execution success)
success = notify.send("Server Alert", "CPU temperature is too high!", urgency="critical", block=True)

# 4. Custom Cross-Platform Configuration
notify.send(
    title="Meeting Reminder",
    message="Technical review starts at 2:00 PM",
    subtitle="Sprint Sync",       # Supported on macOS and Linux-zenity
    icon="assets/bell.png",       # Automatically converted to absolute path (Windows/Linux)
    sound="sms",                  # Windows SMS tone mapping, default sound on macOS
    urgency="normal",             # Linux urgency levels: 'low', 'normal', 'critical'
    timeout=5,                    # Display timeout in seconds (Linux)
    app_name="yyds-notify"        # Custom application sender name (Windows/Linux)
)
```

### Windows Built-in Sound Mappings (`sound` parameter)
* `"default"`: Default system sound
* `"im"`: Instant message sound
* `"mail"`: Email notification sound
* `"reminder"`: Calendar reminder sound
* `"sms"`: SMS/Text message sound
* `"alarm"`: Looping alarm sound
* `"call"`: Looping ringtone sound

### API Aliases
The following functions are identical aliases for convenience:
* `yyds_notify_os.notify(...)`
* `yyds_notify_os.send(...)`
* `yyds_notify_os.show(...)`

---

## 🛠️ CLI Usage

Once installed, send system notifications directly from your shell:

```bash
# Basic notification
yyds-notify "Notification" "Your build is ready!"

# Specify custom sender name
yyds-notify "Alert" "High memory usage detected!" -a "SystemMonitor"

# Dynamic notification updates using replace-id
yyds-notify "Build Status" "Compiling Module A..." -r "build_job_12"
yyds-notify "Build Status" "Compiling Module B..." -r "build_job_12"

# Complex call with sound and critical urgency
yyds-notify "Error" "Deployment failed!" -s "CI Pipeline" -u critical --sound

# View full help menu
yyds-notify --help
```

---

## 🛡️ Technical Implementation Details

1. **Windows**:
   - Uses PowerShell to interface with the Windows Runtime (WinRT) `ToastGeneric` visual template.
   - All string arguments (title, message, icon path) are passed via **process environment variables** to completely avoid shell injection and encoding/truncation issues (e.g. UTF-8/GBK encoding clashes).
   - Maps `replace_id` to the `ToastNotification.Tag` property for in-place card updates.
   - Gracefully attempts to initialize the ToastNotifier with your custom `app_name`. If it fails (due to unregistered AppUserModelId on older Windows releases), it falls back to a guaranteed registered PowerShell AppID.
   - If WinRT initialization fails, it falls back to the classic balloon tip (`System.Windows.Forms.NotifyIcon`).

2. **macOS**:
   - Detects and utilizes `terminal-notifier` if installed (highly recommended for modern macOS notifications with customizable options).
   - If not available, falls back to AppleScript (`osascript`) routed through the `Finder` application context (`tell application "Finder" to display notification ...`). This allows notifications to be delivered reliably without being silently swallowed by the operating system due to terminal or IDE process permission restrictions.
   - Implements robust escaping for double quotes and backslashes to eliminate script execution failures.

3. **Linux**:
   - Detects `DISPLAY` and `WAYLAND_DISPLAY` environments.
   - If GUI is present, uses `notify-send` with a progressive fallback array (peels off unsupported options like `-r` or `-a` step-by-step if the local `notify-send` version is outdated). Falls back to `zenity --notification` if `notify-send` is completely absent.
   - If headless (no GUI), automatically prints notification details to standard error (`sys.stderr`) prepended with an ASCII bell character (`\a`) to trigger a terminal beep.
