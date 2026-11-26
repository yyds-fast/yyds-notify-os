# Changelog

## 0.4.1

- Avoid blocking on Windows `Popen.stderr_thread` `BufferedReader` locks when cleaning up timed-out subprocesses whose grandchildren inherited `stderr`.
- Notify waiters only when the final idle worker thread exits so idle timers on sibling workers are not reset.
- Pin Python 3.8 and 3.9 CI jobs to `ubuntu-24.04` and make cross-platform unit tests drive-independent and popup-free on Windows/macOS runners.

## 0.4.0

- Fix Windows `ToastGeneric` notifications by loading WinRT `XmlDocument`/`ToastNotification` projections, constructing adaptive `ToastGeneric` XML directly, caching and syncing per-app `HKCU` `AppUserModelId` display names/icons (cleaning up stale `IconUri`), supporting `subtitle` and looping audio, and pre-encoding the PowerShell script.
- Harden Linux and `*BSD` `notify-send` with `--` option termination, XML/Pango body entity escaping, `subtitle` and XDG `sound-name` hint support, positive signed 31-bit `-r` replacement IDs, independent `(base_profile, replace_supported)` capability caching, `DBUS_SESSION_BUS_ADDRESS` session detection, and Zenity 4 `--icon` support.
- Escape all leading `NSUserDefaults` / `terminal-notifier` metacharacters (`\`, `-`, `[`, `(`, `{`, `<`, `"`, `'`) on macOS, map cross-platform sound presets, and pass `osascript` arguments via `argv` without copying `os.environ`.
- Replace `ThreadPoolExecutor` with a lazy, `os.fork()`-safe daemon worker dispatcher that scales up to 4 workers on bursts, retires idle workers automatically, respects explicit `shutdown(wait=False)` at exit, and enforces bounded `shutdown(timeout=5.0)` without indefinite hangs.
- Eliminate unused `stdout` subprocess pipes and prevent post-timeout hangs when grandchild daemons inherit `stderr`.
- Stream character sanitization without prematurely truncating strings containing leading control bytes or internal whitespace, expand `~` in icon paths, and reject directory paths as icons.
- Fix CLI `--sound` disambiguation (including single-dash `-` positional arguments), add `--no-fallback`, validate inputs before `--async` detachment, reap detached child processes without `ResourceWarning`s, and detach `stderr` on FIFO/socket pipes.
- Add PEP 561 `py.typed` metadata, complete type annotations, auto-detect `python3`/`python` in `build.sh`, and expand CI matrix to Linux, macOS, and Windows.

## 0.3.1

- Reject abbreviated CLI options so asynchronous delivery cannot recursively relaunch itself.
- Log asynchronous delivery failures that return `False`, not only raised exceptions.
- Cache the working Linux `notify-send` compatibility profile and invalidate it after a failure.
- Add cross-version CI, modernize package metadata, and make release cleanup opt-in.

## 0.3.0

- Bound asynchronous delivery to 64 pending notification jobs.
- Serialize notifications sharing a `replace_id` and coalesce pending updates.
- Validate delivery options before asynchronous submission and report background exceptions.
- Replace executable-probe subprocesses with `shutil.which` and stop redundant retries after a timeout.
- Support `replace_id` through `terminal-notifier -group` on macOS.
- Make CLI background delivery detach correctly on POSIX, preserve stderr fallback, and accept named sounds.
- Add CLI/async regression tests, package license metadata, and safer release checks.
