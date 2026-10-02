---
description: Run this repository's checks for the files that changed, and report what ran and what did not. Use before committing or opening a PR.
when_to_use: Use on your own after changing code, before committing, and before saying a task is done.
---

## Changed files

- Committed on this branch: !`git diff --name-only "$(git rev-parse -q --verify origin/integration || echo origin/main)"...HEAD`
- Not yet committed: !`git status --short`

## Checks by changed path

Run only the checks that match the changed files. Run them from the repository root.

| Changed path | Check |
|---|---|
| `scripts/`, `tests/`, `.github/` | `python3 -m unittest discover -s tests -p 'test_*.py'` |
| `.github/workflows/`, `.github/actions/` | `actionlint` |
| `terraform/` | `terraform -chdir=terraform fmt -check -recursive`, then `init -backend=false -lockfile=readonly -input=false`, `validate`, and `test` |
| `frontend/` | The `lint`, `build`, and `test` scripts in `frontend/package.json` that exist, after `npm ci` |
| `backend/` | `python -m pytest -q tests` from `backend/` |
| Only `docs/` or top-level `*.md` | No check needed |

## Rules

- If a tool is not installed, such as `actionlint` or `terraform`, say so. Do not report the check as passed.
- Terraform is optional for local work. The Intake stage does not require it; the Dev stage checks it.
- Never run `terraform apply`, `plan`, or any AWS command here. These checks need no cloud credentials.
- If a check fails, show the failing output and stop. Do not change tests to make them pass.

## Report

List each check with **passed**, **failed**, or **not run** and the reason. Name the files that no check covered.
