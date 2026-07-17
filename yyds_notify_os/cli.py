# -*- coding:utf-8 -*-

import argparse
import sys
import logging
from yyds_notify_os.core import notify
from yyds_notify_os.__version__ import __version__, __title__

def main():
    # Setup logger formatting for CLI
    logger = logging.getLogger("yyds_notify_os")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        description="A beautiful, cross-platform command line notification tool.",
        prog="yyds-notify"
    )
    parser.add_argument("title", help="Title of the notification")
    parser.add_argument("message", help="Body/message content of the notification")
    parser.add_argument("-s", "--subtitle", help="Subtitle (supported on macOS/some Linux setups)")
    parser.add_argument("-i", "--icon", help="Icon name or image path (Linux/Windows support)")
    parser.add_argument("-u", "--urgency", choices=["low", "normal", "critical"], default="normal",
                        help="Urgency level (Linux only, default: normal)")
    parser.add_argument("-t", "--timeout", type=int, default=5,
                        help="Notification display timeout in seconds (Linux only, default: 5)")
    parser.add_argument("--sound", action="store_true", help="Play a notification sound")
    parser.add_argument("-a", "--app-name", default="yyds-notify", help="Application name (default: yyds-notify)")
    parser.add_argument("-r", "--replace-id", help="Notification replacement ID to update an existing popup (Linux/Windows only)")
    parser.add_argument("--async", dest="block", action="store_false", default=True,
                        help="Send notification asynchronously (non-blocking) in background")
    parser.add_argument("-v", "--version", action="version", version=f"{__title__} {__version__}")

    args = parser.parse_args()

    # Handle async execution properly for CLI tool to avoid daemon thread getting killed immediately
    if not args.block:
        import subprocess
        # Reconstruct arguments without '--async'
        new_args = [sys.executable, "-m", "yyds_notify_os.cli"]
        for arg in sys.argv[1:]:
            if arg not in ("--async",):
                new_args.append(arg)
        
        # Start a detached background process to do the actual notification
        subprocess.Popen(
            new_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=0x00000008 if sys.platform == "win32" else 0, # DETACHED_PROCESS
        )
        sys.exit(0)

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
            block=args.block,
            fallback_to_print=True
        )
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
    
    # Return exit code 0 on success, 1 on failure
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
