#!/usr/bin/env python3
"""Run server-validated site commands concurrently and retain a final summary."""

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading


def run_task(task, lock):
    site = task["site"]
    if task.get("skip"):
        return site, "SKIPPED: " + task["skip"]
    with lock:
        print(f"\n=== {site} ===", flush=True)
    try:
        with subprocess.Popen(task["argv"], cwd=task["cwd"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", bufsize=1) as process:
            for line in process.stdout:
                with lock:
                    print(f"[{site}] {line}", end="", flush=True)
            code = process.wait()
        status = "OK" if code == 0 else f"FAILED: exit {code}"
    except OSError as exc:
        status = f"FAILED: {exc}"
        with lock:
            print(f"[{site}] {status}", file=sys.stderr, flush=True)
    return site, status


def run(tasks):
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        results = list(executor.map(lambda task: run_task(task, lock), tasks))
    print("\n=== Multi action summary ===", flush=True)
    for site, status in results:
        print(f"{site}: {status}", flush=True)
    return int(any(status.startswith("FAILED") for _, status in results))


if __name__ == "__main__":
    sys.exit(run(json.loads(sys.argv[1])))
