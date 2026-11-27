# -*- coding: utf-8 -*-
"""Run a command, show its output in the console AND append it to a log file (for troubleshooting)."""
import os
import subprocess
import sys
import time


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: tee_run.py LOGFILE COMMAND [ARGS...]")
        return 2
    log_path, cmd = sys.argv[1], sys.argv[2:]
    if cmd[0].lower() in ("python", "python.exe"):
        cmd[0] = sys.executable
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} cwd={os.getcwd()}\n===== {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = _decode(raw)
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()
        except KeyboardInterrupt:
            proc.terminate()
        proc.wait()
        log.write(f"===== exit code {proc.returncode}\n")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
