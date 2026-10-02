# Claude Code — club-res-website

@AGENTS.md

`AGENTS.md` above holds the shared rules. This file covers only what is specific to Claude Code.

## First run

Start `claude` in the repository once and accept the trust dialog. Until you do, Claude Code ignores the `allow` rules below; the `deny` and `ask` rules apply either way.

## Skills for contributors

| Command | Use it to |
|---|---|
| `/grill-me <plan>` | Agree a plan before building: one question at a time, with a recommended answer each |
| `/start-feature [topic]` | Create `feature/<your-login>` from the shared base branch |
| `/verify` | Run the checks for what you changed; Claude Code v2.1.286+ also runs it before each commit |
| `/open-pr` | Check, push, and open a PR with a release label; a maintainer merges it |

Bundled `/code-review` and `/security-review` review a diff. Use them before `/open-pr`.

## What `.claude/settings.json` blocks

- Pushes to `main`, `integration`, `dev`, or `prod`, and force pushes.
- `terraform apply`, `destroy`, `import`, and `state` commands.
- Reading `.env` secrets, Terraform state, and `*.tfvars` files.

It asks before any `git push`, `gh pr merge`, or `aws` command.

These rules match the command text Claude writes; they are a guard against mistakes, not a security boundary. GitHub branch rules and AWS IAM are the real controls. Branch rules are not active yet; see [docs/CI_CD.md](docs/CI_CD.md).

## Personal settings

Put personal preferences in `CLAUDE.local.md` and `.claude/settings.local.json`. Git ignores both. Do not use them to remove the shared deny rules.

Read the relevant sections of [docs/CI_CD.md](docs/CI_CD.md) when needed, not the whole file.
