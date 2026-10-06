# Delivery sequence and guardrails

Reviewed from the three-workflow baseline on `develop` on 2026-09-11. The consolidation does not change the Actions sidebar until it reaches the default branch. This page describes repository code; it does not establish current GitHub settings, the deployed revision, or AWS resource state. Historical findings remain in [the original application review](FRONTEND-AND-MEMBERSHIP-REVIEW.md) and [the dated AWS audit](AWS-READONLY-AUDIT-2026-09-09.md).

## Four Actions entries, three repository workflows

The intended Actions sidebar contains **Dependabot Updates**, **Intake**, **Beta · Develop to Feature**, and **Production · Feature to Main**. Dependabot Updates is GitHub's hosted dependency service. Only `intake.yml`, `beta.yml`, and `production.yml` belong in `.github/workflows/`. Shared checks and shipping steps live in `.github/actions/`, so Terraform, security, image, static publishing, and promotion appear as conditional boxes inside a stage rather than extra sidebar workflows. Promotion runs the trusted `scripts/promote.py` from the default branch with `GITHUB_TOKEN` and explicit dispatch; no GitHub App, fourth repository workflow, or new `sync/*` PRs are needed.

| Stage | Candidate verification | After the destination branch moves |
| --- | --- | --- |
| Intake | Topic PR into develop: compile/lint/build, focused authorization and delivery regressions, dependency/secret checks, and affected AWS/Terraform validation; no browser matrix, coverage artifact, image, credentials, or deploy | Develop push/dispatch advances promotion without repeating tests; never deploys |
| Beta | Dispatch on develop checks the candidate against feature, including image when affected | Feature push/dispatch builds and scans the runtime image; deploys only when a separate beta host is explicitly configured |
| Production | Dispatch on feature checks every boundary for executable changes against main | Main push/dispatch builds and scans the runtime image, then requires production environment approval before shipping |

### Dependabot Updates

```mermaid
flowchart LR
  A[Monthly grouped npm update] --> B[One PR to develop]
  B --> C[Intake]
  C --> D{Checks and dependency file allowlist pass?}
  D -- Yes --> E[Merge and close PR]
  D -- No --> F[Keep PR open with failure evidence]
```

### Intake

```mermaid
flowchart LR
  A[Topic PR to develop] --> B[Classify patch or standard lane]
  B --> C[Build and lint affected app]
  B --> D[Focused identity, sender and quota tests]
  B --> E[Conditional AWS and Terraform validation]
  B --> F[Secrets, workflow and dependency checks]
  C --> G[Account for every selected box]
  D --> G
  E --> G
  F --> G
  G --> H{Draft, hold, rejection, stale head?}
  H -- No --> I[Merge to develop]
  H -- Yes --> J[Stop]
```

### Beta · Develop to Feature

```mermaid
flowchart LR
  A[Verified develop revision] --> B[Full affected backend suite]
  A --> C[Full browser and accessibility suite]
  A --> D[Conditional IaC tests]
  A --> E[Security and critical coverage]
  A --> F[Build, exercise and scan image]
  B --> G[Beta required gate]
  C --> G
  D --> G
  E --> G
  F --> G
  G --> H[Merge to feature]
  H --> I[Rebuild exact feature image]
  I --> J{Separate beta host configured?}
  J -- Yes --> K[Deploy beta and verify health]
  J -- No --> L[Record verified, not deployed]
```

### Production · Feature to Main

```mermaid
flowchart LR
  A[Verified feature revision] --> B[Expand executable diff to every boundary]
  B --> C[All backend tests and critical coverage]
  B --> D[All frontend, browser and accessibility tests]
  B --> E[Security, IaC and container proof]
  C --> F[Production required gate]
  D --> F
  E --> F
  F --> G[Merge to main]
  G --> H[Build and verify exact image digest]
  H --> I[Production environment approval]
  I --> J[Deploy, health check and rollback guard]
  J --> K[Record release and synchronize develop]
  L[Reviewed Terraform plan or apply] -. main-only conditional operation .-> E
  M[Static publish after approved cutover] -. main-only conditional operation .-> I
```

The beta-host omission is an explicit current allowance, not proof that beta was deployed. `BETA_AWS_INSTANCE_ID` must identify a different host from the production instance `i-09a071e22270b027c`. Beta runtime also requires disabled email delivery and its own data/configuration. Never reuse production as beta.

A quick patch follows the same automatic branch progression. Documentation and presentation/test-only changes are identified as a patch lane; runtime, dependencies, workflows, Docker and infrastructure remain standard. Intake and Beta select affected checks, while Production expands every executable change to the complete gate. The post-merge shipping phase repeats the image scan and container regressions rather than the entire candidate suite. Terraform does not run for an unrelated application patch.

## Skips and failure handling

Every selected check must succeed. The aggregate jobs reject missing, failed, or unexpectedly skipped checks. Their contexts are deliberately distinct: `intake-required-checks`, `feature-required-checks`, and `production-required-checks`. A lower stage can never satisfy a higher branch rule. A superseded cancelled run does not start promotion. Each stage explains whether it is verifying or shipping and whether a runtime change exists.

Native pushes compare against their recorded before SHA. A verification dispatch compares against its target branch. A shipping dispatch compares its revision to its first parent; it must not classify all existing application files as newly changed. Missing dispatch history fails classification rather than guessing. Checkout fetches the history needed for these comparisons.

Local composite actions must be present before the runner loads them: every caller checks out first. Promotion is different: its privileged job checks out the default branch and invokes the script directly, so first rollout does not depend on a new composite already existing on main. Jobs default to read-only tokens; only promotion, release, and deployment jobs receive the permissions they need.

Bot promotion PRs use explicit stage dispatch rather than a second approval-gated `pull_request` run. Dependabot retains its real Intake PR checks. Successful same-repository topic PRs and allowlisted Dependabot PRs merge without an opt-in label. A draft, hold label, requested changes, foreign fork, stale head, source that does not contain the current base, unexpected dependency file, or closed unmerged candidate stops automatic merging. Merges bind to the checked head and never use an admin bypass. After the aggregate gate and last-moment immutable base/head checks pass, the trusted promotion job publishes the stage-specific commit status on the verified head. Strict up-to-date branch rules invalidate that status when either side changes. History-only commits move from develop to feature so Feature retains Main ancestry; a file-identical feature never creates another Main merge and synchronization loop. When an existing promotion PR follows a newer source revision, the controller dispatches that exact revision instead of leaving stale checks attached.

Dependabot is limited to monthly grouped npm version updates targeting `develop`, with one open version-update PR and major updates excluded. Python automatic version PRs are disabled; actions/Terraform update polling is absent. Successful allowlisted updates merge and close through Intake; failures stay open with their evidence. GitHub-owned Dependabot and Copilot entries are separate from repository workflow files. To show exactly four sidebar entries, disable the account/repository Copilot workflow entry; deleting YAML cannot remove it.

## AWS and Terraform operations

Existing application delivery remains CloudFront VPC origin → EC2 → containerized FastAPI/SPA, with SQLite on retained EBS. S3 stores object bytes; permission-filtered database metadata powers the document/project frontend. A VPC is not a substitute for per-member and per-project authorization.

Never redeploy the replacement-sensitive `YucgOutreach-dev` app stack as part of an application release. Shipping uses ECR credentials through the helper, a checked archive and immutable digest, the explicitly bound stack/instance, and SSM. Preflight checks require encrypted volumes, IMDSv2 and no world-open ingress. Restart verifies the retained database mount, takes an online backup, checks non-root write access and supports application rollback. These code checks need real configured IAM permissions and cannot prove live access from local tests.

Production dispatch offers `deliver`, `plan`, `apply`, and `publish-static`. Maintenance operations are main-only and serialized with delivery. Plan/apply use separate protected infrastructure environments and scoped roles; new storage stays disabled by default. Terraform does not automatically adopt CDK-owned resources or change existing compute/database ownership.

A plan binds the reviewed commit, account, region, state bucket/key, target, SHA-256 and creation time. Saved plans and failure diagnostics stay in encrypted private S3, not public Actions artifacts. Apply uses the exact reviewed saved plan, checks freshness, and takes the state lock. Local Terraform validation and mock tests cannot prove live IAM, ownership, quotas or connectivity. Review the full plan and controlled integration evidence before cutover; follow [the Terraform handoff guide](../terraform/README.md).

`publish-static` is useful only after an approved private-S3/CloudFront-origin cutover. It requires a successful Production delivery run for the exact revision and consumes the frontend extracted from that run's image. It does not rebuild an unrelated frontend or run on every application release. Missing/expired artifacts fail; they are not silently rebuilt. Today the container-hosted SPA remains the documented baseline.

## Acceptance and remaining work

- Behavior and authorization tests must cover sender identity, duplicate claims, invitation admission, private/project access, share expiry and quota races. A shared log must never select another member's Gmail credentials.
- Coverage gates apply individually to critical modules; historical whole-backend coverage is only about one third. Passing scanners or coverage does not establish secure code or good UX. Test deletion, coverage exclusions and policy changes need explicit review.
- Studio, invitations, private documents, sender isolation and first-party tracking already exist in source. Avoid rebuilding them from the historical review. Live Gmail/S3 checks still need explicitly authorized controlled accounts and files.
- Browser telemetry requires an authenticated active member and accepts only bounded `page_view` records. Direct API regressions reject forged quota/activity names, oversized details and oversized batches, keeping server reservation events outside the browser namespace.
- Verify the exact hosted stage results before declaring the consolidation operational. The Actions sidebar change requires the deletion commit to reach main. This local review did not push, merge, approve a release, or change GitHub/AWS settings.
- Preserve the current sole-maintainer arrangement: automated checks and explicit production approval. A second independent maintainer is deferred, as requested.

Cost delta for this local consolidation: **$0 in deployed AWS resources**, before credits. A future beta host, S3 versions/backups/requests/egress, ECR retention and Bedrock calls have separate gross costs. Record those assumptions and deltas in review text; credits are temporary offsets, not a lower operating cost or budget cap.
