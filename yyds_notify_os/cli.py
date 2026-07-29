# -*- coding:utf-8 -*-

import argparse
import logging
import subprocess
import sys

from yyds_notify_os.__version__ import __title__, __version__
from yyds_notify_os.core import notify


def _configure_logging():
    logger = logging.getLogger("yyds_notify_os")
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)

    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return logger


def _positive_timeout(value):
    try:
        timeout = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 1 <= timeout <= 86400:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 86400 seconds")
    return timeout


def _build_parser():
    parser = argparse.ArgumentParser(
        description="A lightweight, cross-platform command line notification tool.",
        prog="yyds-notify",
        allow_abbrev=False,
    )
    parser.add_argument("title", help="Title of the notification")
    parser.add_argument("message", help="Body/message content of the notification")
    parser.add_argument(
        "-s", "--subtitle", help="Subtitle (supported on macOS/some Linux setups)"
    )
    parser.add_argument(
        "-i", "--icon", help="Icon name or image path (Linux/Windows support)"
    )
    parser.add_argument(
        "-u",
        "--urgency",
        choices=["low", "normal", "critical"],
        default="normal",
        help="Urgency level (Linux only, default: normal)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=_positive_timeout,
        default=5,
        help="Notification display timeout in seconds (Linux only, default: 5)",
    )
    parser.add_argument(
        "--sound",
        nargs="?",
        const=True,
        default=False,
        metavar="NAME",
        help="Play the default sound or an optional platform sound name",
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
        "--async",
        dest="async_mode",
        action="store_true",
        help="Start notification delivery in a detached background process",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"{__title__} {__version__}"
    )
    return parser


def _spawn_detached(raw_args):
    command = [sys.executable, "-m", "yyds_notify_os.cli"]
    positional_only = False
    for arg in raw_args:
        if arg == "--":
            positional_only = True
            command.append(arg)
        elif arg != "--async" or positional_only:
            command.append(arg)

    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        # Keep stderr attached so the documented terminal fallback remains
        # visible when the graphical backend fails.
        "stderr": None,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(command, **popen_kwargs)


def main(argv=None):
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.async_mode:
        raw_args = sys.argv[1:] if argv is None else list(argv)
        try:
            _spawn_detached(raw_args)
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
            fallback_to_print=True,
        )
    except ValueError as error:
        sys.stderr.write(f"Error: {error}\n")
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
