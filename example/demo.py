#!/usr/bin/env python
# -*- coding:utf-8 -*-

"""
yyds-notify-os usage demo
This script demonstrates the main features of the yyds-notify-os library.
"""

import os
import sys
import time

# Ensure we can import yyds_notify_os even if not installed globally
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import yyds_notify_os as notify


def demonstrate_basic():
    print("1. Sending a basic notification (asynchronous & non-blocking by default)...")
    # Non-blocking: returns instantly while notification is shown in the background
    notify.send(
        title="Python Task Status",
        message="Your background job has started successfully."
    )
    print("   [Done] Basic notification requested.\n")
    time.sleep(2)  # Short pause to let it register


def demonstrate_custom_styling():
    print("2. Sending a notification with custom options (blocking mode)...")
    
    # We can use standard Linux icon names (e.g. 'dialog-information', 'alarm')
    # or an absolute/relative file path to a .png/.jpg file.
    icon_name = "dialog-information"
    
    success = notify.send(
        title="Important Meeting",
        message="Sprint planning starts in 10 minutes.",
        subtitle="Sprint Sync",                # macOS & zenity support
        icon=icon_name,                        # Linux icon name or file path
        sound="reminder",                      # Play reminder sound (Windows sound mapping, default macOS)
        urgency="critical",                    # Linux urgency levels: low, normal, critical
        timeout=10,                            # Linux timeout in seconds
        app_name="DemoApp",                    # Custom sender/application name
        block=True                             # Wait until the notification subprocess finishes
    )
    print(f"   [Done] Custom notification completed (Success status: {success})\n")
    time.sleep(2)


def demonstrate_dynamic_updates():
    print("3. Demonstrating dynamic notification updates (replace_id)...")
    print("   This updates the same notification card instead of sending new popups.")
    
    task_id = "demo_progress_task"
    total_steps = 5
    
    for i in range(1, total_steps + 1):
        percent = i * (100 // total_steps)
        # Create a basic progress-like message
        message = f"Downloading update files... {percent}%"
        # We can append a small text bar
        bar_length = 10
        filled = int(bar_length * i / total_steps)
        progress_bar = "[" + "=" * filled + " " * (bar_length - filled) + "]"
        
        print(f"   Updating progress: {percent}% {progress_bar}")
        
        notify.send(
            title="System Updater",
            message=f"{message}\n{progress_bar}",
            replace_id=task_id,                # Key to update/replace existing notification
            icon="software-update-available",  # Linux system icon name
            block=True                         # Block here so updates happen in order
        )
        time.sleep(1.5)
        
    print("   [Done] Dynamic updates completed.\n")


def main():
    print("========================================")
    print("   yyds-notify-os Feature Demonstration ")
    print("========================================\n")
    
    demonstrate_basic()
    demonstrate_custom_styling()
    demonstrate_dynamic_updates()
    
    print("All Python API demonstrations finished!")


if __name__ == "__main__":
    main()
