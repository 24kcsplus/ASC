#!/usr/bin/env bash
set -euo pipefail
ASC_MCP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASC_MCP_PYTHON="${ASC_MCP_PYTHON:-$ASC_MCP_DIR/.venv-mcp/bin/python}"
if [[ ! -x "$ASC_MCP_PYTHON" ]]; then
  printf '%s\n' '请先运行 ./install-mcp.sh 安装独立 MCP 环境。' >&2
  exit 1
fi
export PYTHONPATH="$ASC_MCP_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "$ASC_MCP_PYTHON" -m asc_mcp "$@"
