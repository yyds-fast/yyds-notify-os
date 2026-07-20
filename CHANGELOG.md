# Changelog

## 0.3.0

- Bound asynchronous delivery to 64 pending notification jobs.
- Serialize notifications sharing a `replace_id` and coalesce pending updates.
- Validate delivery options before asynchronous submission and report background exceptions.
- Replace executable-probe subprocesses with `shutil.which` and stop redundant retries after a timeout.
- Support `replace_id` through `terminal-notifier -group` on macOS.
- Make CLI background delivery detach correctly on POSIX, preserve stderr fallback, and accept named sounds.
- Add CLI/async regression tests, package license metadata, and safer release checks.
