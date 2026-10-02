# Claude Code — club-res-website

@AGENTS.md

`AGENTS.md` is the shared engineering contract for this repository.
Do not duplicate its rules here or import machine-specific files from another checkout.

Read relevant sections of [docs/CI_CD.md](docs/CI_CD.md) on demand; do not preload the entire architecture document for unrelated edits.
Use Claude's available tools to follow the shared workflow; OMP-specific tool names are not required.
Do not assume custom hooks, skills, agents, or permission controls exist unless their configuration is present.

This file supplies instructions, not security enforcement. GitHub rules and AWS IAM remain separate controls.
Keep durable architecture decisions in `docs/CI_CD.md`; keep tool-specific notes here only when necessary.
