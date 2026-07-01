import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_SH = ROOT / "start.sh"


def copy_start_script(tmp_path: Path) -> Path:
    script = tmp_path / "start.sh"
    script.write_text(START_SH.read_text(), encoding="utf-8")
    script.chmod(0o755)
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "main.py").write_text("app = object()\n", encoding="utf-8")
    return script


def make_fake_bin(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    python = fake_bin / "python3"
    python.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"venv\" ]; then\n"
        "  venv_dir=\"$3\"\n"
        "  mkdir -p \"$venv_dir/bin\"\n"
        "  cat > \"$venv_dir/bin/activate\" <<ACTIVATE\n"
        "export VIRTUAL_ENV=\"$venv_dir\"\n"
        "export PATH=\"$venv_dir/bin:$PATH\"\n"
        "ACTIVATE\n"
        "  cp \"$0\" \"$venv_dir/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"pip\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"uvicorn\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    npx = fake_bin / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo \"$*\" >> \"$NPX_LOG\"\n"
        "mkdir -p \"$HOME/.claude/skills/web-access/scripts\"\n"
        "printf '%s\\n' '// fake check-deps' > \"$HOME/.claude/skills/web-access/scripts/check-deps.mjs\"\n",
        encoding="utf-8",
    )
    npx.chmod(0o755)
    return fake_bin


def run_start(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = copy_start_script(tmp_path)
    fake_bin = make_fake_bin(tmp_path)
    run_env = os.environ.copy()
    run_env.update(env)
    run_env["HOME"] = str(tmp_path / "home")
    run_env["PATH"] = f"{fake_bin}{os.pathsep}{run_env['PATH']}"
    run_env["NPX_LOG"] = str(tmp_path / "npx.log")
    run_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    run_env["HOST"] = "127.0.0.1"
    run_env["PORT"] = "9999"
    return subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env=run_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_start_installs_web_access_once_and_maps_codex_path(tmp_path: Path):
    result = run_start(tmp_path, {})

    assert result.returncode == 0, result.stderr
    claude_skill = tmp_path / "home/.claude/skills/web-access"
    codex_skill = tmp_path / "home/.codex/skills/web-access"
    assert (claude_skill / "scripts/check-deps.mjs").exists()
    assert codex_skill.is_symlink()
    assert codex_skill.resolve() == claude_skill.resolve()
    assert (tmp_path / "npx.log").read_text(encoding="utf-8") == "skills add eze-is/web-access\n"


def test_start_reuses_existing_codex_install_and_maps_claude_path(tmp_path: Path):
    home = tmp_path / "home"
    existing = home / ".codex/skills/web-access/scripts"
    existing.mkdir(parents=True)
    (existing / "check-deps.mjs").write_text("// existing", encoding="utf-8")

    result = run_start(tmp_path, {"HOME": str(home)})

    assert result.returncode == 0, result.stderr
    codex_skill = home / ".codex/skills/web-access"
    claude_skill = home / ".claude/skills/web-access"
    assert claude_skill.is_symlink()
    assert claude_skill.resolve() == codex_skill.resolve()
    assert not (tmp_path / "npx.log").exists()
