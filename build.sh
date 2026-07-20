#!/bin/bash

set -euo pipefail

# 清理并构建可复现的发布产物。
rm -rf build dist yyds_notify_os.egg-info

python -m build
python -m twine check dist/*

if [[ "${1:-}" == "--upload" ]]; then
    python -m twine upload dist/*
else
    echo "Build verified. Pass --upload to upload the artifacts."
fi
