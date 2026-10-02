---
description: Switch to the contributor's one working branch, feature/<github-login>, and bring it up to date. Creates it only if it does not exist yet. Never creates any other branch.
when_to_use: Use on your own before the first file edit in a session when the current branch is not feature/<github-login>. A hook blocks edits on main, integration, dev, and prod.
allowed-tools: Bash(git fetch *) Bash(git switch *) Bash(git merge *) Bash(gh api user *)
---

## Current state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Shared base branches on origin: !`git ls-remote --heads origin integration main`

## Rule

Each contributor has exactly **one** branch: `feature/<login>`. All of their work goes there, one commit after another.

- Never add a topic or suffix, such as `feature/<login>-search`.
- Never create a second branch for a new task.
- Never rebase or force-push this branch. It is shared with its open PR.

## Steps

1. Run `git fetch origin`.
2. Get my GitHub login with `gh api user --jq .login`. If `gh` is not signed in, stop and tell me to run `gh auth login`. The branch is `feature/<login>`.
3. Choose the base: `origin/integration` when it exists, otherwise `origin/main`. When it is `main`, tell me once that `integration`, `dev`, and `prod` are not set up yet.
4. Get onto the branch:
   - Already on `feature/<login>`: stay.
   - It exists locally or on origin: `git switch feature/<login>`.
   - It exists nowhere: `git switch -c feature/<login> <base>`. This happens once per contributor.
   Uncommitted changes move with the switch. If git refuses, stop and show me why.
5. Bring it up to date, in this order. Stop at a conflict, run `git merge --abort`, and show me the conflicting files.
   - `git merge --ff-only origin/feature/<login>` when origin has commits the local branch lacks, such as work from another computer.
   - `git merge --no-edit <base>`, so the branch includes everything already merged by others.
6. Do not push. Continue with the task. When it is complete, use the `open-pr` skill.
