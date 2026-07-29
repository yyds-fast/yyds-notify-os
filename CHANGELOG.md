# Changelog

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
