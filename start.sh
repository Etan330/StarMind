#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

cd "$ROOT_DIR"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 Python。请先安装 Python 3.9+，或用 PYTHON=/path/to/python ./start.sh 指定路径。" >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "正在创建虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "StarMind 启动中: http://$HOST:$PORT"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
