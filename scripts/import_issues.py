#!/usr/bin/env python3
"""Import GitHub issues from CSV using the supported GitHub CLI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


REQUIRED_COLUMNS = {"external_id", "title"}
OPTIONAL_COLUMNS = {"body", "labels", "assignees", "milestone", "state"}
ALLOWED_STATES = {"open", "closed"}


def run_gh(arguments: list[str], token: str | None = None) -> str:
    environment = os.environ.copy()
    if token:
        environment["GH_TOKEN"] = token
    completed = subprocess.run(
        ["gh", *arguments],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"gh {' '.join(arguments[:2])} failed: {detail}")
    return completed.stdout.strip()


def existing_imports(repository: str) -> dict[str, str]:
    output = run_gh(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/issues?state=all&per_page=100",
        ]
    )
    pages = json.loads(output)
    imports: dict[str, str] = {}
    prefix = "<!-- csv-import-id: "
    for page in pages:
        for issue in page:
            body = issue.get("body") or ""
            for line in body.splitlines():
                if line.startswith(prefix) and line.endswith(" -->"):
                    external_id = line[len(prefix) : -4].strip()
                    imports[external_id] = issue["html_url"]
    return imports


def split_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def github_graphql(token: str, query: str, variables: dict[str, object]) -> dict:
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "csv-issue-import-poc",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {error.code}: {detail}") from error
    if payload.get("errors"):
        messages = "; ".join(error["message"] for error in payload["errors"])
        raise RuntimeError(f"GitHub GraphQL failed: {messages}")
    return payload["data"]


def add_to_project(project_url: str, issue_url: str, token: str) -> None:
    project_match = re.fullmatch(
        r"https://github\.com/(users|orgs)/([^/]+)/projects/(\d+)/?", project_url
    )
    if not project_match:
        raise ValueError(
            "project URL must look like "
            "https://github.com/users|orgs/OWNER/projects/NUMBER"
        )
    issue_match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?", issue_url
    )
    if not issue_match:
        raise ValueError(f"invalid GitHub issue URL: {issue_url}")

    owner_type, project_owner, project_number = project_match.groups()
    repo_owner, repo, issue_number = issue_match.groups()
    owner_field = "user" if owner_type == "users" else "organization"
    lookup = github_graphql(
        token,
        f"""
        query($projectOwner: String!, $projectNumber: Int!, $repoOwner: String!,
              $repo: String!, $issueNumber: Int!) {{
          projectOwner: {owner_field}(login: $projectOwner) {{
            projectV2(number: $projectNumber) {{ id }}
          }}
          repository(owner: $repoOwner, name: $repo) {{
            issue(number: $issueNumber) {{ id }}
          }}
        }}
        """,
        {
            "projectOwner": project_owner,
            "projectNumber": int(project_number),
            "repoOwner": repo_owner,
            "repo": repo,
            "issueNumber": int(issue_number),
        },
    )
    project_id = lookup["projectOwner"]["projectV2"]["id"]
    issue_id = lookup["repository"]["issue"]["id"]
    github_graphql(
        token,
        """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }
        """,
        {"projectId": project_id, "contentId": issue_id},
    )
    print(f"PROJECT {issue_url}: {project_url}")


def validate_row(row: dict[str, str], row_number: int) -> None:
    if None in row:
        raise ValueError(f"row {row_number}: contains more values than the header")
    missing_cells = [name for name, value in row.items() if value is None]
    if missing_cells:
        raise ValueError(
            f"row {row_number}: contains fewer values than the header: {missing_cells}"
        )
    external_id = row.get("external_id", "").strip()
    title = row.get("title", "").strip()
    state = row.get("state", "open").strip().lower() or "open"
    if not external_id:
        raise ValueError(f"row {row_number}: external_id is required")
    if not title:
        raise ValueError(f"row {row_number}: title is required")
    if state not in ALLOWED_STATES:
        raise ValueError(
            f"row {row_number}: state must be one of {sorted(ALLOWED_STATES)}"
        )


def create_issue(repository: str, row: dict[str, str]) -> str:
    external_id = row["external_id"].strip()
    title = row["title"].strip()
    body = row.get("body", "").strip()
    marker = f"<!-- csv-import-id: {external_id} -->"
    full_body = f"{body}\n\n{marker}" if body else marker

    arguments = [
        "issue",
        "create",
        "--repo",
        repository,
        "--title",
        title,
        "--body",
        full_body,
    ]
    for label in split_values(row.get("labels", "")):
        arguments.extend(["--label", label])
    for assignee in split_values(row.get("assignees", "")):
        arguments.extend(["--assignee", assignee])
    milestone = row.get("milestone", "").strip()
    if milestone:
        arguments.extend(["--milestone", milestone])

    issue_url = run_gh(arguments)
    if (row.get("state", "open").strip().lower() or "open") == "closed":
        run_gh(
            [
                "issue",
                "close",
                issue_url,
                "--repo",
                repository,
                "--reason",
                "completed",
            ]
        )
    return issue_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="OWNER/REPO; defaults to GITHUB_REPOSITORY",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--project-url",
        default=os.environ.get("PROJECT_URL", ""),
        help="optional user or organization GitHub Project URL",
    )
    args = parser.parse_args()

    if not args.repository or "/" not in args.repository:
        parser.error("--repository must be OWNER/REPO or GITHUB_REPOSITORY must be set")
    if not args.csv_path.is_file():
        parser.error(f"CSV file does not exist: {args.csv_path}")
    if not args.dry_run and not os.environ.get("GH_TOKEN"):
        parser.error("GH_TOKEN is required unless --dry-run is used")
    project_token = os.environ.get("PROJECT_TOKEN", "")
    if args.project_url and not args.dry_run and not project_token:
        parser.error("PROJECT_TOKEN is required when --project-url is set")

    with args.csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - headers
        unknown = headers - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
        if missing:
            parser.error(f"missing required CSV columns: {sorted(missing)}")
        if unknown:
            parser.error(f"unknown CSV columns: {sorted(unknown)}")
        rows = list(reader)

    if not rows:
        parser.error("CSV contains no issue rows")
    for row_number, row in enumerate(rows, start=2):
        validate_row(row, row_number)

    known_imports = {} if args.dry_run else existing_imports(args.repository)
    seen_in_file: set[str] = set()
    created = 0
    skipped = 0

    for row_number, row in enumerate(rows, start=2):
        external_id = row["external_id"].strip()
        if external_id in seen_in_file:
            raise ValueError(f"row {row_number}: duplicate external_id {external_id!r}")
        seen_in_file.add(external_id)

        if external_id in known_imports:
            if args.project_url:
                add_to_project(
                    args.project_url, known_imports[external_id], project_token
                )
            print(f"SKIP {external_id}: already imported")
            skipped += 1
            continue
        if args.dry_run:
            print(f"DRY RUN {external_id}: {row['title'].strip()}")
            continue

        issue_url = create_issue(args.repository, row)
        print(f"CREATED {external_id}: {issue_url}")
        known_imports[external_id] = issue_url
        if args.project_url:
            add_to_project(args.project_url, issue_url, project_token)
        created += 1

    if args.dry_run:
        print(f"Validated {len(rows)} issue rows; no issues created.")
    else:
        print(f"Import complete: {created} created, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
