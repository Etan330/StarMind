#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
CLAUDE_WEB_ACCESS_DIR="$HOME/.claude/skills/web-access"
CODEX_WEB_ACCESS_DIR="$HOME/.codex/skills/web-access"
WEB_ACCESS_REPO="eze-is/web-access"

cd "$ROOT_DIR"

has_web_access() {
  [ -f "$1/scripts/check-deps.mjs" ]
}

link_web_access_dir() {
  local source_dir="$1"
  local target_dir="$2"

  if [ -e "$target_dir" ] || [ -L "$target_dir" ]; then
    if has_web_access "$target_dir"; then
      return 0
    fi
    echo "检测到 $target_dir 已存在，但不是可用的 web-access skill，已跳过映射。" >&2
    return 0
  fi

  mkdir -p "$(dirname "$target_dir")"
  ln -s "$source_dir" "$target_dir"
}

ensure_web_access_skill() {
  if has_web_access "$CLAUDE_WEB_ACCESS_DIR"; then
    link_web_access_dir "$CLAUDE_WEB_ACCESS_DIR" "$CODEX_WEB_ACCESS_DIR"
    return 0
  fi

  if has_web_access "$CODEX_WEB_ACCESS_DIR"; then
    link_web_access_dir "$CODEX_WEB_ACCESS_DIR" "$CLAUDE_WEB_ACCESS_DIR"
    return 0
  fi

  if ! command -v npx >/dev/null 2>&1; then
    echo "未找到 npx，无法自动安装 web-access skill。请先安装 Node.js 22+，再运行: npx skills add $WEB_ACCESS_REPO" >&2
    exit 1
  fi

  echo "正在安装 web-access skill: $WEB_ACCESS_REPO"
  npx skills add "$WEB_ACCESS_REPO"

  if ! has_web_access "$CLAUDE_WEB_ACCESS_DIR"; then
    echo "web-access skill 安装后未在 $CLAUDE_WEB_ACCESS_DIR 找到 check-deps.mjs。请检查 skills CLI 的安装路径。" >&2
    exit 1
  fi

  link_web_access_dir "$CLAUDE_WEB_ACCESS_DIR" "$CODEX_WEB_ACCESS_DIR"
}

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
ensure_web_access_skill

echo "StarMind 启动中: http://$HOST:$PORT"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
