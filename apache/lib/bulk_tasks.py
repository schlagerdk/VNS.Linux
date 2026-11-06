#!/usr/bin/env python3
"""Run server-validated site commands sequentially and retain a final summary."""

import json
import subprocess
import sys


def run(tasks):
    results = []
    for task in tasks:
        site = task["site"]
        if task.get("skip"):
            results.append((site, "SKIPPED: " + task["skip"]))
            continue
        print(f"\n=== {site} ===", flush=True)
        try:
            result = subprocess.run(task["argv"], cwd=task["cwd"])
            status = "OK" if result.returncode == 0 else f"FAILED: exit {result.returncode}"
        except OSError as exc:
            status = f"FAILED: {exc}"
            print(status, file=sys.stderr, flush=True)
        results.append((site, status))
    print("\n=== Multi action summary ===", flush=True)
    for site, status in results:
        print(f"{site}: {status}", flush=True)
    return int(any(status.startswith("FAILED") for _, status in results))


if __name__ == "__main__":
    sys.exit(run(json.loads(sys.argv[1])))
