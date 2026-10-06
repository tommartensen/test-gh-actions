#!/usr/bin/env python3
"""Find open PRs targeting release-* branches and re-run check-release-pr for each."""

import json
import subprocess
import sys
from collections import Counter


WORKFLOW_FILE = "check-release-pr.yaml"
WORKFLOW_NAME = "Check PR to release-* branch"


def main() -> int:
    owner, repo = run_gh("repo", "view", "--json", "owner,name", "-q", ".owner.login + \" \" + .name").split()

    prs = list_release_prs(owner, repo)
    if not prs:
        print("No open PRs targeting release-* branches found.")
        return 0

    counts: Counter[str] = Counter()
    failures: list[str] = []

    for pr in prs:
        number = pr["number"]
        base = pr["baseRefName"]
        url = pr["url"]

        run = latest_check_release_pr_run(number, owner, repo)
        if run is None:
            failures.append(
                f"PR #{number} ({url}): no {WORKFLOW_NAME} run found"
            )
            continue

        run_id = run["runId"]
        if run["status"] in {"queued", "in_progress", "waiting", "requested", "pending"}:
            print(
                f"Skipping PR #{number} → {base}: {WORKFLOW_NAME} run {run_id} "
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


def run_gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def list_release_prs(owner: str, repo: str) -> list[dict]:
    query = """
    query($owner: String!, $repo: String!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequests(first: 100, states: OPEN, after: $cursor) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            number
            url
            baseRefName
            headRefName
            headRefOid
            isCrossRepository
          }
        }
      }
    }
    """

    prs = []
    cursor = None

    while True:
        args = ["api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"repo={repo}"]
        if cursor:
            args.extend(["-F", f"cursor={cursor}"])

        raw = run_gh(*args)
        data = json.loads(raw)

        pull_requests = data["data"]["repository"]["pullRequests"]
        prs.extend(
            {
                "number": pr["number"],
                "url": pr["url"],
                "baseRefName": pr["baseRefName"],
                "headRefName": pr["headRefName"],
                "headRefOid": pr["headRefOid"],
            }
            for pr in pull_requests["nodes"]
            if pr["baseRefName"].startswith("release-") and not pr["isCrossRepository"]
        )

        if not pull_requests["pageInfo"]["hasNextPage"]:
            break
        cursor = pull_requests["pageInfo"]["endCursor"]

    return prs


def latest_check_release_pr_run(pr_number: int, owner: str, repo: str) -> dict | None:
    query = """
    query($owner: String!, $repo: String!, $pr_number: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr_number) {
          commits(last: 1) {
            nodes {
              commit {
                checkSuites(first: 100, after: $cursor) {
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                  nodes {
                    status
                    conclusion
                    workflowRun {
                      databaseId
                      workflow {
                        name
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    try:
        cursor = None

        while True:
            args = [
                "api", "graphql",
                "-f", f"query={query}",
                "-F", f"owner={owner}",
                "-F", f"repo={repo}",
                "-F", f"pr_number={pr_number}",
            ]
            if cursor:
                args.extend(["-F", f"cursor={cursor}"])

            raw = run_gh(*args)
            data = json.loads(raw)

            commits = data.get("data", {}).get("repository", {}).get("pullRequest").get("commits", {}).get("nodes", [])
            if not commits:
                return None

            check_suites_data = commits[0].get("commit", {}).get("checkSuites", {})
            check_suites = check_suites_data.get("nodes", [])

            for suite in check_suites:
                workflow_run = suite.get("workflowRun")
                if not workflow_run:
                    continue

                if workflow_run["workflow"]["name"] != WORKFLOW_NAME:
                    continue

                return {
                    "runId": workflow_run["databaseId"],
                    "status": suite.get("status", ""),
                }

            # If we found nothing and there are more pages, continue with the next page
            page_info = check_suites_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return None

    except subprocess.CalledProcessError:
        return None


def rerun_workflow(run_id: int) -> None:
    run_gh("run", "rerun", str(run_id))


if __name__ == "__main__":
    sys.exit(main())
