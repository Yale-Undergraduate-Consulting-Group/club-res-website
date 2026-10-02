---
description: Interview the contributor about a plan, one question at a time, until the plan is clear enough to build. Use before nontrivial work.
argument-hint: "[what you want to build or change]"
disable-model-invocation: true
---

Plan to examine: $ARGUMENTS

Interview me about this plan until we share one clear understanding of it. Do not write or edit code during the interview.

## How to interview

- Ask **one question at a time**. Wait for my answer before the next one.
- With each question, give your recommended answer and the reason, so I can reply "yes" when I agree.
- If the code or docs can answer a question, look it up instead of asking. Say what you found.
- Follow each answer down its decision branch. Resolve dependencies between decisions in order.
- Challenge vague answers. "It should be secure" or "make it fast" needs a concrete behavior.
- Stop when every branch below has an answer or an explicit "not needed because …".

## Decision branches for this repository

Read [AGENTS.md](../../../AGENTS.md) and the relevant parts of [docs/CI_CD.md](../../../docs/CI_CD.md) first.

1. **Who and why.** Which club members use this, for what task, and how do we know it worked?
2. **Reuse.** Does one of the existing club sites already do this? What do we copy instead of rebuild?
3. **Data exposure.** What data does this show or store? The current site is public static output: anything in the build can reach any browser. Client or NDA material cannot go there.
4. **Runtime.** Does it work as a static page? A server, login, database, or API route has no host yet; adding one is a separate, reviewed design.
5. **AWS impact.** Does it add or change AWS resources, IAM, or recurring cost? Which account: dev, prod, or both?
6. **Delivery.** It ships through `feature/<user> → integration → dev → prod`. What must work locally, and what changes only at dev (static export, Terraform)?
7. **Verification.** Which checks prove it works? Which failure or permission case must be tested?
8. **Rollback.** If prod breaks, how do we undo it? What happens to data already written?
9. **Release.** Which label: `release:major`, `release:minor`, or `release:patch`?

## When the interview ends

Write a short plan with these parts:

- **Goal** in one sentence.
- **Decisions** with their reasons.
- **Out of scope** items.
- **Open questions** that still need someone else, such as a maintainer or the AWS account owner.
- **First step** to build.

Then ask if I want to start. Do not start until I say yes.
