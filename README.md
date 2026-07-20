# yyds-notify-os

A lightweight and reliable cross-platform desktop notification library for Python.

[中文说明 (Chinese README)](https://github.com/yyds-fast/yyds-notify-os/blob/main/README_CN.md)

---

## 💡 Key Features

* **Zero Python Runtime Dependencies**: Uses only the standard library. Delivery is delegated to platform tools such as PowerShell, AppleScript, and `notify-send`, with automatic fallback when optional tools are unavailable.
* **Bounded Async Dispatch**: Uses up to four background workers and 64 pending slots, preventing unbounded memory growth during notification bursts.
* **Ordered Replacement/Updating (`replace_id`)**: Updates sharing an ID are serialized, and not-yet-started updates are coalesced to the latest value so progress cannot move backwards.
* **Modern Windows Customizations**: Uses Microsoft's modern `ToastGeneric` template to support custom application icons (`icon`) and mapped system sounds (`sound`) or mute configurations.
* **Path Auto-Resolution**: Converts existing local icon paths to absolute paths before background delivery.
* **Observable Failures and Fallbacks**: Headless environments and delivery failures fall back to stderr with an ASCII bell; unexpected background errors are reported through the `yyds_notify_os` logger.
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

You can find runnable examples in the [example/](https://github.com/yyds-fast/yyds-notify-os/tree/main/example) directory:
* [demo.py](https://github.com/yyds-fast/yyds-notify-os/blob/main/example/demo.py): Python API, custom settings, and dynamic updates using `replace_id`.
* [demo.sh](https://github.com/yyds-fast/yyds-notify-os/blob/main/example/demo.sh): Command-line parameters and replacement updates.

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
    sound="sms",                  # Windows SMS mapping; treated as a sound name on macOS
    urgency="normal",             # Linux urgency levels: 'low', 'normal', 'critical'
    timeout=5,                    # Display timeout in seconds (Linux)
    app_name="yyds-notify"        # Custom application sender name (Windows/Linux)
)
```

An asynchronous call returning `True` means the task was accepted by the dispatcher; use `block=True` when you need the system command's delivery result. Invalid `urgency`, `timeout`, `sound`, and `replace_id` values raise `ValueError` before the task is queued.

### Windows Built-in Sound Mappings (`sound` parameter)
* `"default"`: Default system sound
* `"im"`: Instant message sound
* `"mail"`: Email notification sound
* `"reminder"`: Calendar reminder sound
* `"sms"`: SMS/Text message sound
* `"alarm"`: System alarm sound
* `"call"`: System call sound

### API Aliases
The following functions are identical aliases for convenience:
* `yyds_notify_os.notify(...)`
* `yyds_notify_os.send(...)`
* `yyds_notify_os.show(...)`

Long-running applications normally do not need to manage the worker pool. Call `yyds_notify_os.shutdown(wait=True, timeout=5)` to stop accepting asynchronous work early; later asynchronous requests fall back to synchronous delivery.

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

# Select a platform sound and deliver from a detached process
yyds-notify "Build Complete" "Artifacts are ready" --sound reminder --async

# View full help menu
yyds-notify --help
```

---

## 🛡️ Technical Implementation Details

1. **Windows**:
   - Uses PowerShell to interface with the Windows Runtime (WinRT) `ToastGeneric` visual template.
   - All string arguments (title, message, icon path) are passed via **process environment variables** to completely avoid shell injection and encoding/truncation issues (e.g. UTF-8/GBK encoding clashes).
   - Maps `replace_id` to the `ToastNotification.Tag` property for in-place card updates.
   - Attempts to initialize the ToastNotifier with the requested `app_name`, then falls back to a known PowerShell AppID when the system rejects it.
   - If WinRT initialization fails, it falls back to the classic balloon tip (`System.Windows.Forms.NotifyIcon`).

2. **macOS**:
   - Detects and utilizes `terminal-notifier` if installed and maps `replace_id` to its `-group` option.
   - If not available, falls back to AppleScript (`osascript`) routed through the `Finder` application context (`tell application "Finder" to display notification ...`). This allows notifications to be delivered reliably without being silently swallowed by the operating system due to terminal or IDE process permission restrictions.
   - Passes text through process environment variables or argument arrays instead of interpolating it into shell commands.

3. **Linux**:
   - Detects `DISPLAY` and `WAYLAND_DISPLAY` environments.
   - If GUI is present, uses `notify-send` with a progressive fallback array (peels off unsupported options like `-r` or `-a` step-by-step if the local `notify-send` version is outdated). Falls back to `zenity --notification` if `notify-send` is completely absent.
   - If headless (no GUI), automatically prints notification details to standard error (`sys.stderr`) prepended with an ASCII bell character (`\a`) to trigger a terminal beep.
