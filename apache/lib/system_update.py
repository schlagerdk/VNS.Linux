"""Run an admin update and persist its outcome across the service restart."""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time


def write_status(path, data):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_update(path, job, command):
    try:
        process = subprocess.Popen(command, cwd="/", start_new_session=True)
        try:
            code = process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        state = {"id": job, "status": "success" if code == 0 else "error",
                 "error": "" if code == 0 else f"Update exited with code {code}.",
                 "finished": time.time()}
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Update failed: {exc}", file=sys.stderr, flush=True)
        state = {"id": job, "status": "error", "error": str(exc), "finished": time.time()}
    write_status(path, state)


if __name__ == "__main__":
    run_update(Path(sys.argv[1]), sys.argv[2], sys.argv[3:])
