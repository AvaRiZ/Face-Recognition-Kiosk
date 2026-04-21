from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _has_database_url_arg(args: list[str]) -> bool:
    lowered = [a.lower() for a in args]
    return "-databaseurl" in lowered or any(a.startswith("-databaseurl:") for a in lowered)


def _read_env_value(file_path: Path, key: str) -> str:
    if not file_path.exists():
        return ""
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def _bootstrap_local_env(repo_root: Path) -> None:
    env_local = repo_root / ".env.local"
    env_example = repo_root / ".env.local.example"

    if env_local.exists() or not env_example.exists():
        return

    sample_db_url = _read_env_value(env_example, "DATABASE_URL")
    looks_like_placeholder = (not sample_db_url) or ("<" in sample_db_url and ">" in sample_db_url)
    if looks_like_placeholder:
        return

    shutil.copyfile(env_example, env_local)
    print("Created .env.local from .env.local.example")


def main() -> int:
    script_path = Path(__file__).with_name("start_system.ps1")
    if not script_path.exists():
        print(f"Missing launcher script: {script_path}", file=sys.stderr)
        return 1

    repo_root = script_path.parent.parent
    args = sys.argv[1:]

    if "DATABASE_URL" not in os.environ and not _has_database_url_arg(args):
        _bootstrap_local_env(repo_root)

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        print("PowerShell executable not found (pwsh/powershell).", file=sys.stderr)
        return 1

    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        *args,
    ]

    completed = subprocess.run(command)
    if completed.returncode != 0:
        print(
            "Hint: set DATABASE_URL in .env.local, or pass -DatabaseUrl \"postgresql://...\".",
            file=sys.stderr,
        )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
