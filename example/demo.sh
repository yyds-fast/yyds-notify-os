#!/bin/bash

# yyds-notify-os CLI usage demo
# This script demonstrates how to use the yyds-notify command line interface.

# Make sure to run this script from the project root or make sure the package is installed:
# pip install -e .

echo "========================================"
echo "   yyds-notify-os CLI Demonstration     "
echo "========================================"
echo ""

# Find the CLI executable. If not installed in python path, we can run it via python module.
if command -v yyds-notify &> /dev/null; then
    NOTIFY_CMD="yyds-notify"
else
    echo "yyds-notify CLI command not found in PATH."
    echo "Running CLI via Python module: python -m yyds_notify_os.cli"
    NOTIFY_CMD="python -m yyds_notify_os.cli"
fi

echo "1. Basic notification via CLI..."
$NOTIFY_CMD "CLI Notification" "Hello from the terminal!"
sleep 2

echo "2. CLI notification with custom application name, urgency, and icon..."
$NOTIFY_CMD "CLI Custom" "This is a normal urgency alert." \
    -a "TerminalDemo" \
    -u normal \
    -i dialog-information \
    -t 8
sleep 2

echo "3. CLI notification updates using replacement ID..."
TASK_ID="cli_progress_task"
for i in {1..5}; do
    percent=$((i * 20))
    bar="["
    for ((j=1; j<=i; j++)); do bar="${bar}="; done
    for ((j=i+1; j<=5; j++)); do bar="${bar} "; done
    bar="${bar}]"
    
    echo "Updating: ${percent}% ${bar}"
    $NOTIFY_CMD "CLI Progress" "Status: ${percent}% completed\n${bar}" \
        -r "$TASK_ID" \
        -i software-update-available
    sleep 1
done

echo ""
echo "CLI Demonstration finished!"
