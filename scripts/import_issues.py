#!/usr/bin/env python3
"""Import GitHub issues from CSV using the supported GitHub CLI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_COLUMNS = {"external_id", "title"}
OPTIONAL_COLUMNS = {"body", "labels", "assignees", "milestone", "state"}
ALLOWED_STATES = {"open", "closed"}


def run_gh(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["gh", *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"gh {' '.join(arguments[:2])} failed: {detail}")
    return completed.stdout.strip()


def existing_import_ids(repository: str) -> set[str]:
    output = run_gh(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/issues?state=all&per_page=100",
        ]
    )
    pages = json.loads(output)
    markers: set[str] = set()
    prefix = "<!-- csv-import-id: "
    for page in pages:
        for issue in page:
            body = issue.get("body") or ""
            for line in body.splitlines():
                if line.startswith(prefix) and line.endswith(" -->"):
                    markers.add(line[len(prefix) : -4].strip())
    return markers


def split_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


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
    args = parser.parse_args()

    if not args.repository or "/" not in args.repository:
        parser.error("--repository must be OWNER/REPO or GITHUB_REPOSITORY must be set")
    if not args.csv_path.is_file():
        parser.error(f"CSV file does not exist: {args.csv_path}")
    if not args.dry_run and not os.environ.get("GH_TOKEN"):
        parser.error("GH_TOKEN is required unless --dry-run is used")

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

    known_ids = set() if args.dry_run else existing_import_ids(args.repository)
    seen_in_file: set[str] = set()
    created = 0
    skipped = 0

    for row_number, row in enumerate(rows, start=2):
        external_id = row["external_id"].strip()
        if external_id in seen_in_file:
            raise ValueError(f"row {row_number}: duplicate external_id {external_id!r}")
        seen_in_file.add(external_id)

        if external_id in known_ids:
            print(f"SKIP {external_id}: already imported")
            skipped += 1
            continue
        if args.dry_run:
            print(f"DRY RUN {external_id}: {row['title'].strip()}")
            continue

        issue_url = create_issue(args.repository, row)
        print(f"CREATED {external_id}: {issue_url}")
        known_ids.add(external_id)
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
