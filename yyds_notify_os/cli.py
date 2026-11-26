# -*- coding:utf-8 -*-

from __future__ import annotations

import argparse
import logging
import os
import stat
import subprocess
import sys
import threading
from typing import List, Optional, Sequence

from yyds_notify_os.__version__ import __title__, __version__
from yyds_notify_os.core import _prepare_notification_args, notify

_STDLIB_POPEN = subprocess.Popen

_VALUE_OPTIONS = frozenset(
    (
        "-s",
        "--subtitle",
        "-i",
        "--icon",
        "-u",
        "--urgency",
        "-t",
        "--timeout",
        "-a",
        "--app-name",
        "-r",
        "--replace-id",
    )
)


class _StderrStreamHandler(logging.StreamHandler):
    """StreamHandler that dynamically resolves the current `sys.stderr`."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("yyds_notify_os")
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)

    if not logger.handlers:
        handler = _StderrStreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)

    if logger.level == logging.NOTSET:
        logger.setLevel(logging.WARNING)
    return logger


def _positive_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 1 <= timeout <= 86400:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 86400 seconds")
    return timeout


def _is_option_token(token: str) -> bool:
    """In `argparse`, a single `'-'` is a positional value rather than a flag."""
    return len(token) > 1 and token.startswith("-")


def _normalize_cli_argv(raw_args: Sequence[str]) -> List[str]:
    """
    Disambiguates `--sound` (`nargs="?"`) when placed before positional
    `title` / `message` arguments without an explicit sound name
    (e.g., `yyds-notify --sound "Title" "Message"` or `yyds-notify --sound - "Msg"`).
    """
    tokens = list(raw_args)
    if "--sound" not in tokens:
        return tokens

    sep_index = tokens.index("--") if "--" in tokens else len(tokens)
    head = tokens[:sep_index]
    tail = tokens[sep_index:]
    if "--sound" not in head:
        return tokens

    ambiguous_sound_indices: List[int] = []
    free_non_option_count = max(0, len(tail) - 1)

    idx = 0
    while idx < len(head):
        tok = head[idx]
        if tok in _VALUE_OPTIONS:
            idx += 2
            continue
        if tok == "--sound":
            if idx + 1 < len(head) and not _is_option_token(head[idx + 1]):
                ambiguous_sound_indices.append(idx)
                free_non_option_count += 1
                idx += 2
            else:
                idx += 1
            continue
        if _is_option_token(tok):
            idx += 1
            continue
        free_non_option_count += 1
        idx += 1

    if ambiguous_sound_indices and free_non_option_count <= 2:
        ambiguous_set = set(ambiguous_sound_indices)
        rebuilt_head = [t for i, t in enumerate(head) if i not in ambiguous_set]
        rebuilt_head.append("--sound")
        return rebuilt_head + tail

    return tokens


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A lightweight, cross-platform command line notification tool.",
        prog="yyds-notify",
        allow_abbrev=False,
    )
    parser.add_argument("title", help="Title of the notification")
    parser.add_argument("message", help="Body/message content of the notification")
    parser.add_argument("-s", "--subtitle", help="Subtitle (Windows, macOS, and Linux)")
    parser.add_argument(
        "-i", "--icon", help="Icon name or image path (Linux/Windows/macOS support)"
    )
    parser.add_argument(
        "-u",
        "--urgency",
        choices=["low", "normal", "critical"],
        default="normal",
        help="Urgency level (Linux/Windows, default: normal)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=_positive_timeout,
        default=5,
        help="Notification display timeout in seconds (1..86400, default: 5)",
    )
    parser.add_argument(
        "--sound",
        nargs="?",
        const=True,
        default=False,
        metavar="NAME",
        help="Play the default sound or an optional platform/preset sound name",
    )
    parser.add_argument(
        "-a",
        "--app-name",
        default="yyds-notify",
        help="Application name (default: yyds-notify)",
    )
    parser.add_argument(
        "-r",
        "--replace-id",
        help="Replacement ID for updating a popup (Windows, Linux, terminal-notifier)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable terminal bell/stderr text fallback when GUI delivery fails",
    )
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Start notification delivery in a detached background process",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"{__title__} {__version__}"
    )
    return parser


def _should_detach_stderr(no_fallback: bool = False) -> bool:
    """
    Keep stderr attached on interactive terminals and regular files so the
    terminal fallback remains visible, while detaching FIFO/socket pipes so
    callers capturing output (`2>&1` / `capture_output=True`) do not block.
    """
    if no_fallback:
        return True
    try:
        mode = os.fstat(sys.stderr.fileno()).st_mode
    except (AttributeError, OSError, ValueError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


def _reap_detached_process(proc: subprocess.Popen) -> None:
    """
    Reaps a detached child in the background when the parent stays alive,
    preventing zombie processes and `Popen.__del__` `ResourceWarning`s without
    delaying CLI exit (daemon thread).
    """
    try:
        proc.wait()
    except Exception:
        pass


def _spawn_detached(raw_args: Sequence[str]) -> subprocess.Popen:
    command = [sys.executable, "-m", "yyds_notify_os.cli"]
    positional_only = False
    no_fallback = False

    for arg in raw_args:
        if arg == "--" and not positional_only:
            positional_only = True
            command.append(arg)
            continue
        if not positional_only and arg == "--no-fallback":
            no_fallback = True
        if arg != "--async" or positional_only:
            command.append(arg)

    stderr_target = (
        subprocess.DEVNULL if _should_detach_stderr(no_fallback=no_fallback) else None
    )
    popen_kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_target,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)
    if isinstance(proc, _STDLIB_POPEN):
        try:
            threading.Thread(
                target=_reap_detached_process,
                args=(proc,),
                name="yyds_notify_cli_reaper",
                daemon=True,
            ).start()
        except RuntimeError:
            pass
    return proc


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_logging()
    parser = _build_parser()
    raw_args = sys.argv[1:] if argv is None else list(argv)
    effective_args = _normalize_cli_argv(raw_args)
    args = parser.parse_args(effective_args)

    fallback_to_print = not args.no_fallback

    if args.async_mode:
        try:
            _prepare_notification_args(
                title=args.title,
                message=args.message,
                subtitle=args.subtitle,
                icon=args.icon,
                urgency=args.urgency,
                timeout=args.timeout,
                sound=args.sound,
                app_name=args.app_name,
                replace_id=args.replace_id,
                block=True,
                fallback_to_print=fallback_to_print,
            )
        except ValueError as error:
            sys.stderr.write(f"Error: {error}\n")
            return 1

        try:
            _spawn_detached(effective_args)
        except OSError as error:
            sys.stderr.write(
                f"Error: unable to start background notification: {error}\n"
            )
            return 1
        return 0

    try:
        success = notify(
            title=args.title,
            message=args.message,
            subtitle=args.subtitle,
            icon=args.icon,
            urgency=args.urgency,
            timeout=args.timeout,
            sound=args.sound,
            app_name=args.app_name,
            replace_id=args.replace_id,
            block=True,
            fallback_to_print=fallback_to_print,
        )
    except ValueError as error:
        sys.stderr.write(f"Error: {error}\n")
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
