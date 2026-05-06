from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def _wait_for_port(host: str, port: int, timeout_seconds: int = 90) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                time.sleep(1.0)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch Library Face Access System host stack.")
    parser.add_argument("--host", default=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FLASK_RUN_PORT", "5000")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-log-window", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(repo_root)))
    logs_dir = local_appdata / "FaceRecognitionKiosk" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "host-stack.log"
    log_file.touch(exist_ok=True)

    env = os.environ.copy()
    env.setdefault("FLASK_RUN_HOST", args.host)
    env.setdefault("FLASK_RUN_PORT", str(args.port))

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with log_file.open("a", encoding="utf-8") as stream:
        stream.write(f"\n\n=== Launch at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        host_proc = subprocess.Popen(
            [sys.executable, "-m", "app.host_stack"],
            cwd=str(repo_root),
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    if os.name == "nt" and not args.no_log_window:
        tail_command = f"Get-Content -Path '{log_file}' -Wait"
        subprocess.Popen(
            ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", tail_command],
            cwd=str(repo_root),
        )

    if _wait_for_port(args.host, args.port):
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}")
        print(f"Kiosk started (PID {host_proc.pid}).")
        print(f"App URL: http://{args.host}:{args.port}")
        print(f"Log file: {log_file}")
    else:
        exit_code = host_proc.poll()
        print("Kiosk process started, but web server did not become ready before timeout.")
        if exit_code is not None:
            print(f"Host process exited early with code {exit_code}.")
        print(f"Check logs: {log_file}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
