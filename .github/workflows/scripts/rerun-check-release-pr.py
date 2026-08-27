#!/usr/bin/env python3
"""Find open PRs targeting release-* branches and re-run check-release-pr for each."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter


WORKFLOW_FILE = "check-release-pr.yaml"


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
        "repos/{owner}/{repo}/pulls?state=open&per_page=100",
        "--paginate",
        "--slurp",
    )
    # Flatten paginated results (--slurp wraps pages in an outer array)
    pages = json.loads(raw)
    prs = [pr for page in pages for pr in page]
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


def latest_backport_run(pr_number: int) -> dict | None:
    # For pull_request_target workflows, the run SHA comes from the base branch,
    # not the PR head, so we must match by PR number instead of commit SHA
    # Use the API directly to get pull_requests field
    raw = run_gh(
        "api",
        f"repos/{{owner}}/{{repo}}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=20",
        "--paginate",
        "--slurp",
    )
    pages = json.loads(raw)
    for page in pages:
        for run in page.get("workflow_runs", []):
            # Match runs associated with this PR number
            pull_requests = run.get("pull_requests", [])
            if any(pr["number"] == pr_number for pr in pull_requests):
                return {"databaseId": run["id"], "status": run["status"]}
    return None


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
        url = pr["url"]

        run = latest_backport_run(number)
        if run is None:
            failures.append(
                f"PR #{number} ({url}): no check-release-pr run found"
            )
            continue

        run_id = run["databaseId"]
        if run["status"] in {"queued", "in_progress", "waiting", "requested", "pending"}:
            print(
                f"Skipping PR #{number} → {base}: check-release-pr run {run_id} "
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
        print(f"Re-triggered check-release-pr for PR #{number} → {base} (run {run_id})")

    print()
    print("Successfully re-triggered check-release-pr:")
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
