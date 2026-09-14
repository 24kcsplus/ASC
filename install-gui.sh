#!/usr/bin/env bash
set -euo pipefail
ASC_GUI_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "$ASC_GUI_DIR/.venv"
"$ASC_GUI_DIR/.venv/bin/python" -m pip install -r "$ASC_GUI_DIR/requirements-gui.txt"
printf '%s\n' '安装完成，使用 ./run-gui.sh 或 ./run-gui.sh /path/to/app.apk 启动。'
