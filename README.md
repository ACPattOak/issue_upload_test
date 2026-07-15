# Bulk-upload GitHub issues from a CSV

This proof of concept lets a product manager prepare issues in Excel, convert
them to the expected CSV format, and create them in GitHub by committing one
file. The repository's GitHub Actions workflow performs the upload.

## Why this exists

GitHub does not provide a good native bulk uploader for creating Issues from an
Excel workbook or CSV. Issues can be created individually in the website or
through APIs and command-line tools, but neither is a practical product-manager
workflow for a large backlog.

This repository provides a controlled bridge:

1. Prepare and review the backlog in Excel.
2. Convert it to the documented CSV structure.
3. Commit the CSV to `main`.
4. Let GitHub Actions create the issues and, when configured, add them to a
   GitHub Project.

## Product manager guide

### 1. Prepare your Excel workbook

1. Download [`examples/issues.csv`](examples/issues.csv) from this repository.
   In GitHub, open the file and use **Download raw file**.
2. Keep your original Excel workbook unchanged as a backup.
3. Give both files to your organisation's approved version of Microsoft
   Copilot:
   - Your Excel workbook.
   - The downloaded example `issues.csv`.
4. Use a prompt such as:

   > Convert my Excel backlog into a CSV that exactly matches the headings,
   > column order, quoting and formatting in the supplied issues.csv example.
   > Keep every issue, give every row a unique and stable external_id, and do
   > not invent missing assignees, labels or milestones. Return a downloadable
   > UTF-8 CSV file.

5. Ask Copilot to check that:
   - Every row has a unique `external_id` and a `title`.
   - The headings exactly match the example, including lower-case spelling.
   - Cells containing commas, quotation marks or multiple lines are correctly
     quoted as CSV.
   - `state` is either `open` or `closed`.
   - Labels, assignees and milestones already exist in the destination
     repository.

Only upload business data to an AI service approved by your organisation.

### 2. Replace the repository CSV

Committing this file to `main` starts a real import. It is not a preview.

1. Open the CSV produced by Copilot in Notepad or another plain-text editor.
2. Select everything and copy it.
3. In GitHub, open [`examples/issues.csv`](examples/issues.csv).
4. Select **Edit this file** using the pencil icon.
5. Select all the existing text and paste your new CSV over it.
6. Check that the first row contains the expected headings.
7. Select **Commit changes** and commit directly to the `main` branch.
8. Open the repository's **Actions** tab and select **Import issues from CSV**
   to watch the run.
9. When the run is green, check the repository's **Issues** tab and the
   configured GitHub Project.

Important behaviour:

- A push that changes `examples/issues.csv` on `main` performs a real import.
- A manually started workflow defaults to dry-run mode and creates nothing
  unless **Validate without creating issues** is changed to `false`.
- An `external_id` is used only once. Reusing one skips that row; it does not
  update the existing issue.
- A row with `state` set to `closed` is created and immediately closed, so it
  appears under **Closed** rather than **Open** issues.
- If any row is invalid, the workflow fails instead of silently ignoring it.

## CSV structure

| Column | Required | Format |
| --- | --- | --- |
| `external_id` | Yes | Unique, stable ID used to prevent duplicate imports. |
| `title` | Yes | GitHub Issue title. |
| `body` | No | Markdown. CSV quoting is required for commas, quotes or multiple lines. |
| `labels` | No | Comma-separated existing labels inside one quoted CSV cell. |
| `assignees` | No | Comma-separated GitHub usernames with repository access. |
| `milestone` | No | Existing milestone title. |
| `state` | No | `open` or `closed`; defaults to `open`. |

Column names are exact and case-sensitive. Unknown columns fail validation so
spreadsheet changes cannot silently discard information.

## Manual validation and import

The workflow can still be run manually from **Actions** → **Import issues from
CSV** → **Run workflow**. Manual runs default to dry-run mode. A dry run prints
the rows it validated and ends with `no issues created`.

To perform a manual real import, change **Validate without creating issues** to
`false`. The optional `project_url` input overrides the repository's configured
`PROJECT_URL` variable for that run.

## How to use this repository in your organisation

A typical GitHub Enterprise organisation has several repositories and one or
more GitHub Projects. A Project tracks issues and pull requests from those
repositories; repositories can also be linked to the Project.

For an initial, deliberately simple operating model:

1. Create one repository called `project-management` for each GitHub Project.
2. Fork this repository into the organisation. In plain English, make a copy of
   it under the organisation and use that copy as the Project's
   `project-management` repository.
3. Link the `project-management` repository to the relevant GitHub Project.
4. Configure its `PROJECT_URL` Actions variable and `PROJECT_PAT` Actions
   secret.
5. Product managers replace `examples/issues.csv` in that repository whenever
   they need to load a backlog.

This one-repository-per-Project approach is a starting point, not the intended
final architecture. It makes ownership and permissions easy to understand
while the importer is being proven.

## What needs maturing

### Map the organisation's issue and Project fields

Before treating this as a production importer, analyse the configuration and
layout of each target GitHub Project. Decide which spreadsheet columns should
map to:

- Core Issue data: title, body, labels, assignees, milestone and open/closed
  state.
- Organisation Issue features: Issue type, parent/sub-issue relationships and
  dependencies.
- Project fields: Status, Priority, Iteration, dates, estimates and any custom
  text, number or single-select fields.

Issue fields and Project fields are different. The current importer creates the
Issue and adds it to a Project, but it does not populate custom Project fields.
The analysis should record every field's name, type, allowed values, whether it
is required, and how an Excel value should be transformed when it does not
match.

### Extend and test the importer

Once the mapping is agreed, ask an engineer—or an appropriately supervised
Copilot coding agent—to update `scripts/import_issues.py`, the CSV schema and
the examples. Add automated tests before relying on it for a live backlog.

### Support Projects outside the management repository's normal access

The current Project flow uses `PROJECT_URL` and an encrypted `PROJECT_PAT`.
Mature the authentication and access model if issues must be added to Projects
that the `project-management` repository is not linked to or whose owner is
different. For an organisation-wide service, prefer a dedicated GitHub App with
explicit Projects and Issues permissions over a user-owned PAT.

### Support selecting a destination repository

The current script creates Issues in the repository running the workflow. A
production version should accept an allow-listed destination repository—either
once per workflow or per CSV row—and create the Issue directly there. Creating
directly in the destination is clearer than creating it in `project-management`
and transferring it afterwards. Validation must confirm that the automation
credential can write to the selected repository and that referenced labels,
assignees and milestones are valid there.

## Technical design

The earlier `gavinr/github-csv-tools` package was evaluated, but version 3.2.0
uses GitHub's undocumented legacy `/import/issues` endpoint, launches rows
concurrently and can exit successfully after failures. This PoC instead uses:

- Python's standard `csv` parser for correctly quoted and multiline fields.
- GitHub's maintained `gh issue create` command on GitHub-hosted runners.
- The job's repository-scoped `GITHUB_TOKEN` for Issue creation.
- Sequential creation to reduce secondary-rate-limit risk.
- Invisible `csv-import-id` markers for rerun protection.
- The Projects GraphQL API with the encrypted `PROJECT_PAT` secret when a
  Project URL is configured.

The PoC is linked to [Issue Upload Test](https://github.com/users/ACPattOak/projects/1).
