#!/usr/bin/env bash
set -euo pipefail
ASC_MCP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"${ASC_MCP_BOOTSTRAP_PYTHON:-python3}" -m venv "$ASC_MCP_DIR/.venv-mcp"
"$ASC_MCP_DIR/.venv-mcp/bin/python" -m pip install -r "$ASC_MCP_DIR/requirements-mcp.txt"
