---
description: Check, push, and open a pull request from a feature/<user> branch into the shared base branch, with a release label.
when_to_use: Use on your own when the requested change is complete and the verify skill passes, unless the contributor said not to open a PR yet. The push asks the contributor for approval.
---

## Current state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Shared base branches on origin: !`git ls-remote --heads origin integration main`

## Steps

1. Stop if the branch is not `feature/<name>` with exactly one slash. Tell me to run `/start-feature`.
2. Stop if there are uncommitted changes. Ask me to commit them first.
3. Choose the base: `integration` when it exists on origin, otherwise `main`.
4. Run `git fetch origin`. Read `git diff origin/<base>...HEAD`.
5. Run the `verify` skill. If a check fails, stop and show the failure.
6. Check the diff for things that must not ship:
   - Secrets, tokens, or AWS keys.
   - Client names, client data, or NDA material. The site is public static output.
   - Files that `.gitignore` covers, such as `.env` or Terraform state.
   Stop and show me any finding.
7. Suggest that I run `/code-review` on the diff. Continue when I say so.
8. Ask which release label applies. Recommend one from the diff:
   - `release:major`: full or breaking release (X.0.0).
   - `release:minor`: new feature (0.X.0).
   - `release:patch`: fix or small change (0.0.X). No label also means patch.
9. Push with `git push -u origin HEAD`. This prompts for approval.
10. Create the PR with `gh pr create --base <base> --label <label>`. Fill the body from [the PR template](../../../.github/pull_request_template.md). State which checks ran and which did not.
11. Show me the PR URL. Do not merge it. A maintainer reviews and merges.
