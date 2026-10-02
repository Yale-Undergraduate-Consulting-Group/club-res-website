# Claude Code — club-res-website

@AGENTS.md

`AGENTS.md` above holds the shared rules. This file covers only what is specific to Claude Code.

## First run

Start `claude` in the repository once and accept the trust dialog. Until you do, Claude Code ignores the `allow` rules below; the `deny` and `ask` rules apply either way.

## Default workflow

Follow these steps on your own for every code change. The contributor does not need to ask for them.

1. **Plan.** For a nontrivial change, use the `grill-me` skill before writing code. Skip it for typo fixes, one-line fixes, and doc wording.
2. **Branch.** Before the first edit, if the branch is `main`, `integration`, `dev`, or `prod`, use the `start-feature` skill. A hook blocks edits on those branches.
3. **Build.** Follow `AGENTS.md`: the smallest complete change, with its callers, tests, and docs.
4. **Verify.** Use the `verify` skill after changing code and before saying the task is done.
5. **Review.** Reread the full diff for bugs, leftover debug code, secrets, and client data, and fix what you find. Contributors can also run the bundled `/code-review`.
6. **Open the PR.** When the change is complete and verified, use the `open-pr` skill. The push asks the contributor for approval. A maintainer merges.

Contributors can also run any step directly: `/grill-me`, `/start-feature`, `/verify`, `/open-pr`.

## What `.claude/settings.json` blocks

- Pushes to `main`, `integration`, `dev`, or `prod`, and force pushes.
- `terraform apply`, `destroy`, `import`, and `state` commands.
- Reading `.env` secrets, Terraform state, and `*.tfvars` files.

It asks before any `git push`, `gh pr merge`, or `aws` command.

These rules match the command text Claude writes; they are a guard against mistakes, not a security boundary. GitHub branch rules and AWS IAM are the real controls. Branch rules are not active yet; see [docs/CI_CD.md](docs/CI_CD.md).

## Personal settings

Put personal preferences in `CLAUDE.local.md` and `.claude/settings.local.json`. Git ignores both. Do not use them to remove the shared deny rules.

Read the relevant sections of [docs/CI_CD.md](docs/CI_CD.md) when needed, not the whole file.
