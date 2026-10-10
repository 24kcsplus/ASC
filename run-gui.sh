#!/usr/bin/env bash
set -euo pipefail
ASC_GUI_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$ASC_GUI_DIR/.venv/bin/python" ]]; then
  printf '%s\n' '请先运行 ./install-gui.sh 安装 GUI 依赖。' >&2
  exit 1
fi
export PYTHONPATH="$ASC_GUI_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "$ASC_GUI_DIR/.venv/bin/python" -m asc_gui "$@"
