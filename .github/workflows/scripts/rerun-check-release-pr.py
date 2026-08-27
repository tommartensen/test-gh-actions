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


def get_workflow_runs_by_pr() -> dict[int, dict]:
    """Fetch all workflow runs once and index them by PR number.

    Returns a dict mapping PR number to the latest run for that PR.
    The API returns runs sorted newest-first, so the first match for each PR is the latest.
    """
    raw = run_gh(
        "api",
        f"repos/{{owner}}/{{repo}}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=100",
        "--paginate",
        "--slurp",
    )
    pages = json.loads(raw)

    # Build index: PR number -> latest run
    runs_by_pr: dict[int, dict] = {}
    for page in pages:
        for run in page.get("workflow_runs", []):
            pull_requests = run.get("pull_requests", [])
            for pr in pull_requests:
                pr_number = pr["number"]
                # Only store the first (newest) run for each PR
                if pr_number not in runs_by_pr:
                    runs_by_pr[pr_number] = {
                        "databaseId": run["id"],
                        "status": run["status"],
                    }

    return runs_by_pr


# Look up the latest workflow run for a PR number from the pre-built index.
def latest_check_release_pr_run(pr_number: int, runs_by_pr: dict[int, dict]) -> dict | None:
    return runs_by_pr.get(pr_number)


def rerun_workflow(run_id: int) -> None:
    run_gh("run", "rerun", str(run_id))


def main() -> int:
    prs = list_release_prs()
    if not prs:
        print("No open PRs targeting release-* branches found.")
        return 0

    # Fetch workflow runs once and index by PR number (performance optimization)
    print(f"Fetching workflow runs for {len(prs)} PRs...")
    runs_by_pr = get_workflow_runs_by_pr()
    print(f"Found workflow runs for {len(runs_by_pr)} PRs")

    counts: Counter[str] = Counter()
    failures: list[str] = []

    for pr in prs:
        number = pr["number"]
        base = pr["baseRefName"]
        url = pr["url"]

        run = latest_check_release_pr_run(number, runs_by_pr)
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
