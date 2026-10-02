---
description: Create a contributor branch named feature/<github-login> or feature/<github-login>-<topic> from the current shared base branch.
when_to_use: Use on your own before the first file edit when the current branch is main, integration, dev, or prod. A hook blocks edits on those branches.
argument-hint: "[optional short topic, e.g. search]"
allowed-tools: Bash(git fetch *) Bash(git switch *) Bash(gh api user *)
---

Topic: $ARGUMENTS

## Current state

- Branch: !`git branch --show-current`
- Uncommitted changes: !`git status --short`
- Shared base branches on origin: !`git ls-remote --heads origin integration main`

## Steps

1. If there are uncommitted changes, stop. Ask me to commit or stash them first.
2. Run `git fetch origin`.
3. Get my GitHub login with `gh api user --jq .login`. If `gh` is not signed in, stop and tell me to run `gh auth login`.
4. Build the name: `feature/<login>`, or `feature/<login>-<topic>` when I gave a topic. Use lowercase letters, digits, and hyphens in the topic.
   - Use exactly **one** slash. The Intake workflow rejects nested names such as `feature/jeet/search`.
5. Choose the base:
   - `origin/integration` when it exists.
   - Otherwise `origin/main`. Tell me that `integration`, `dev`, and `prod` are not set up yet, so the staged checks do not run yet.
6. Run `git switch -c <name> <base>`. If the branch already exists, switch to it and ask before resetting anything.
7. Do not push. Continue with the task. When it is complete, use the `open-pr` skill.
