#!/usr/bin/env python3
"""Find open PRs targeting release-* branches and re-run backport-check for each."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter


WORKFLOW_FILE = "backport-check.yaml"


def run_gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def list_release_prs() -> list[dict]:
    raw = run_gh(
        "api",
        "repos/:owner/:repo/pulls?state=open&per_page=100",
        "--paginate",
    )
    prs = json.loads(raw)
    return [
        {
            "number": pr["number"],
            "url": pr["html_url"],
            "baseRefName": pr["base"]["ref"],
            "headRefName": pr["head"]["ref"],
            "headRefOid": pr["head"]["sha"],
        }
        for pr in prs
        if pr["base"]["ref"].startswith("release-")
    ]


def latest_backport_run(head_sha: str) -> dict | None:
    raw = run_gh(
        "run",
        "list",
        "--workflow",
        WORKFLOW_FILE,
        "--commit",
        head_sha,
        "--limit",
        "1",
        "--json",
        "databaseId,status",
    )
    runs = json.loads(raw)
    if not runs:
        return None
    return runs[0]


def rerun_workflow(run_id: int) -> None:
    run_gh("run", "rerun", str(run_id))


def main() -> int:
    prs = list_release_prs()
    if not prs:
        print("No open PRs targeting release-* branches found.")
        return 0

    counts: Counter[str] = Counter()
    failures: list[str] = []

    for pr in prs:
        number = pr["number"]
        base = pr["baseRefName"]
        head_sha = pr["headRefOid"]
        url = pr["url"]

        run = latest_backport_run(head_sha)
        if run is None:
            failures.append(
                f"PR #{number} ({url}): no backport-check run found for {head_sha[:12]}"
            )
            continue

        run_id = run["databaseId"]
        if run["status"] in {"queued", "in_progress", "waiting", "requested", "pending"}:
            print(
                f"Skipping PR #{number} → {base}: backport-check run {run_id} "
                f"is already {run['status']}"
            )
            continue

        try:
            rerun_workflow(run_id)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            failures.append(f"PR #{number} ({url}): failed to re-run {run_id}: {stderr}")
            continue

        counts[base] += 1
        print(f"Re-triggered backport-check for PR #{number} → {base} (run {run_id})")

    print()
    print("Successfully re-triggered backport-check:")
    if counts:
        for base in sorted(counts):
            print(f"  {base}: {counts[base]} PR(s)")
        print(f"  total: {sum(counts.values())} PR(s)")
    else:
        print("  total: 0 PR(s)")

    if failures:
        print()
        print("Skipped / failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
