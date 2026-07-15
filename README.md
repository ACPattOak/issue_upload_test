# CSV issue upload proof of concept

This repository proves that a versioned CSV file can bulk-create GitHub issues through a manually triggered GitHub Actions workflow.

## Why this implementation

The established `gavinr/github-csv-tools` package was evaluated first. It is purpose-built and popular, but version 3.2.0 uses GitHub's undocumented legacy `/import/issues` endpoint, launches every row concurrently, and can exit successfully after import failures. For a repeatable 2026 workflow, this PoC instead uses:

- Python's standard `csv` parser for RFC-style quoted and multiline fields.
- GitHub's maintained `gh issue create` command, already available on GitHub-hosted runners.
- The repository's automatically scoped `GITHUB_TOKEN`; no PAT is stored in this repository.
- Sequential creation to reduce secondary-rate-limit risk.
- Stable `external_id` markers to make reruns skip already imported rows.

## CSV structure

| Column | Required | Format |
| --- | --- | --- |
| `external_id` | Yes | Unique stable ID used for rerun protection. |
| `title` | Yes | GitHub issue title. |
| `body` | No | Markdown; quote the CSV field when it contains commas, quotes, or newlines. |
| `labels` | No | Comma-separated existing label names inside a quoted CSV field. |
| `assignees` | No | Comma-separated GitHub usernames with repository access. |
| `milestone` | No | Existing milestone title. |
| `state` | No | `open` or `closed`; defaults to `open`. |

CSV column names are exact and case-sensitive. Unknown columns fail validation so spreadsheet drift cannot silently discard data. Labels, assignees, and milestones must already exist or be valid for the repository.

## Run it

1. Open **Actions** → **Import issues from CSV** → **Run workflow**.
2. Keep `examples/issues.csv`, or enter another committed CSV path.
3. Run once with **dry_run** enabled to validate the file.
4. Run with **dry_run** disabled to create the issues.

The workflow has only `contents: read` and `issues: write` permissions. Imported issue bodies contain an invisible `csv-import-id` marker; rerunning a file skips matching IDs.

## Local validation

```bash
python scripts/import_issues.py examples/issues.csv \
  --repository ACPattOak/issue_upload_test \
  --dry-run
```

## Projects stretch goal

Adding issues to a GitHub Project is a separate concern from issue import. GitHub maintains [`actions/add-to-project`](https://github.com/actions/add-to-project) for this. It requires a project URL and a token with Projects permissions, so it should be added as a separate `issues: opened` workflow after the basic import is accepted.
