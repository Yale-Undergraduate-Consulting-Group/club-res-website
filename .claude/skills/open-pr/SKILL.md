---
description: Check and push the contributor's feature/<login> branch, then open its pull request into the shared base branch, or update the one already open.
when_to_use: Use on your own when the requested change is complete and the verify skill passes, unless the contributor said not to open a PR yet. The push asks the contributor for approval.
---

## Current state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Shared base branches on origin: !`git ls-remote --heads origin integration main`

## Steps

1. Get my login with `gh api user --jq .login`. Stop if the branch is not exactly `feature/<login>`. Use the `my-branch` skill to switch to it.
2. If there are uncommitted changes from this task, commit them to `feature/<login>`. Stop and ask about changes you did not make.
3. Choose the base: `integration` when it exists on origin, otherwise `main`.
4. Run `git fetch origin`. Read `git diff origin/<base>...HEAD`.
5. Run the `verify` skill. If a check fails, stop and show the failure.
6. Check the diff for things that must not ship:
   - Secrets, tokens, or AWS keys.
   - Client names, client data, or NDA material. The site is public static output.
   - Files that `.gitignore` covers, such as `.env` or Terraform state.
   Stop and show me any finding.
7. Reread the full diff for bugs and leftover debug code, and fix what you find.
8. Choose the release label from the diff, and tell me which and why:
   - `release:major`: full or breaking release (X.0.0).
   - `release:minor`: new feature (0.X.0).
   - `release:patch`: fix or small change (0.0.X). No label also means patch.
9. Push with `git push -u origin feature/<login>`. This prompts for approval.
10. Check for an open PR: `gh pr list --head feature/<login> --state open --json number,url,labels`.
    - **One is open:** the push already updated it. Do not create another. If this change needs a larger label than the PR has, replace it with `gh pr edit <number> --remove-label <old> --add-label <new>`. Add a PR comment that lists what this push added and which checks ran.
    - **None is open:** create one with `gh pr create --base <base> --head feature/<login> --label <label>`. Fill the body from [the PR template](../../../.github/pull_request_template.md). State which checks ran and which did not.
11. Show me the PR URL. Do not merge it. A maintainer reviews and merges. After the merge, keep working on the same branch; the `my-branch` skill brings it up to date.
