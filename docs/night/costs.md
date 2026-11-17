# Costs: measured baseline, dashboard, and reductions

Date: 2026-11-16. AWS evidence is the supplied read-only snapshot collected 2026-11-17 03:54 UTC (`/tmp/aws-cost-facts.md`); no AWS, provider, GitHub, or production-data call was made. Prices are USD list prices seen 2026-11-16. November projections use 30/17 of the supplied November-to-date amount, so they are a pace, not an invoice forecast. Provider invoices and the production SQLite database were not available; those cells say so rather than inventing spend.

## Decision summary

Current AWS pace is **about $29.07/month**. The non-overlapping recommendation set saves about **$7.90/month without a commitment** (run dev only when needed, remove the abandoned test secret) or **$13.59/month with a one-year no-upfront t3.small Instance Savings Plan/Standard RI for prod**. Moving dev secrets to standard SSM Parameter Store after checking the secret is under 4 KiB adds at most **$0.40/month**, making the reviewed range **$7.90–$13.99/month**. This retains every feature, database, outreach target, and record. Do not count the alternative t4g path on top of the t3 commitment.

No cost-saving Terraform change was both safe and reversible now. Volume shrinking replaces storage, key changes require data re-encryption, log removal weakens detection for $0 current saving, and image deletion is irreversible for pennies. The Terraform diff only adds the required read-only Cost Explorer permission and its mocked-provider assertion; it is **not applied** and needs the president's approval. The dashboard job adds up to $0.60/month (two environments × 30 days × $0.01).

## Current AWS bill

### November pace by service

| Service | Nov 1–17 | 30-day pace | Driver and action |
|---|---:|---:|---|
| EC2 compute | $9.707 | **$17.13** | t3.small instance-hours. Stop dev more; keep prod available. |
| EC2 Other | $2.378 | **$4.20** | Mainly four gp3 volumes; $0.082 is snapshots. Keep current sizes. |
| VPC | $2.352 | **$4.15** | Almost entirely public IPv4 ($2.372 usage-type line). Keep: every cheaper-looking replacement costs more or breaks egress. |
| KMS | $1.036 | **$1.83** (steady state about $2) | Two customer keys at $1/key-month. Keep isolation. |
| Secrets Manager | $0.461 | **$0.81** (three secrets steady state $1.20) | Dev, prod, and abandoned `test/yucg-outreach`. Confirm then delete only the test secret. |
| GuardDuty | $0.135 | **$0.24** | 15,906 events plus 0.062 GB analyzed. Keep. |
| Route 53 | $0.124 | **$0.22** | 206,072 DNS Firewall queries. Keep. |
| S3 | $0.081 | **$0.14** | Backups, catalog, documents, site, state, and 114 MB of CloudTrail. Keep lifecycle rules. |
| ECR | $0.028 | **$0.05** | 12 dev + 11 prod images; lifecycle already keeps 10 tagged and expires untagged after 7 days. Keep rollback depth. |
| Cost Explorer | $0.020 | $0.04 at old usage; **$0.60 after dashboard apply** | One $0.01 cached request/day/environment. |
| Tax | $0.150 | $0.26 | Follows taxable usage. |
| **Account total** | **$16.473** | **$29.07** | Supplied snapshot's stated November pace. |

The `Site` tag attributed $6.466 to dev, $7.111 to prod, and $2.895 to shared/untagged usage through the snapshot, a 30-day pace of **$11.41 dev, $12.55 prod, $5.11 shared/untagged**. Cost allocation tags do not attribute every line; these figures do not replace the account total.

Prices: [EC2 on-demand](https://aws.amazon.com/ec2/pricing/on-demand/), [VPC/public IPv4](https://aws.amazon.com/vpc/pricing/), [EBS](https://aws.amazon.com/ebs/pricing/), [KMS](https://aws.amazon.com/kms/pricing/), [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/), [GuardDuty](https://aws.amazon.com/guardduty/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), [ECR](https://aws.amazon.com/ecr/pricing/), [S3](https://aws.amazon.com/s3/pricing/), [Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/).

### Fixed-cost questions

#### Dev only when needed

Dev office hours were enabled on 2026-10-08 (`docs/CI_CD.md`), and the supplied snapshot shows it running during its Monday window. Stopping preserves both volumes. At the current dev pace, fixed storage/key/secret costs are about $3.7/month; avoiding nearly all instance and public-IPv4 hours saves about **$7.5/month**. Two one-hour deploy/test sessions cost only about $0.10 in compute + IPv4. Risk: SSM access and all dev jobs are offline while stopped; deployment preflight currently rejects a stopped box and the deploy role intentionally cannot start it. Implement only after the president chooses either manual start or narrowly grants deploy automation start permission. Do not stop prod: open pixels would be lost and the shared app would be unavailable.

#### Public IPv4: keep it

A running address is $0.005/hour ($3.65/month for always-on prod; dev only while running). The box is in a public subnet with an internet gateway, the free S3 gateway endpoint, and no NAT. It needs arbitrary HTTP(S) egress for ECR, SSM, Anthropic, Slack, Gmail, Monid, Firecrawl, Tavily, SerpAPI, Qualtrics, public registers, and crawled company sites.

- One NAT gateway per VPC is **$32.85/month + $0.045/GB**, versus $3.65 for prod IPv4. Two NAT gateways start at $65.70/month.
- Interface endpoints are $0.01/hour/AZ (**$7.30/month each**). `ssm`, `ssmmessages`, `ec2messages`, `ecr.api`, `ecr.dkr`, Logs, Secrets Manager, and KMS are at least eight endpoints, **$58.40/month/environment**, and still do not reach external APIs or arbitrary sites. The existing S3 gateway endpoint is free.
- An egress-only internet gateway is free, but IPv6-only does not work. `ec2messages` remains IPv4-only; Firecrawl, Tavily, Slack, Companies House, Reddit, Qualtrics, and 9 of 10 sampled company sites had no AAAA record. AWS-managed NAT64/DNS64 uses a charged NAT gateway. ECR and SSM also require new dual-stack endpoint names and ECR layer behavior was not verified.

Dropping IPv4 therefore **increases** the bill by at least $29.20/month per always-on VPC with NAT, or breaks features. Keep it.

Sources: [VPC pricing](https://aws.amazon.com/vpc/pricing/), [PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/), [AWS IPv6 support](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ipv6-support.html), [NAT64/DNS64](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-nat64-dns64.html), [egress-only gateway](https://docs.aws.amazon.com/vpc/latest/userguide/egress-only-internet-gateway.html).

#### KMS: keep customer-managed keys

Two keys cost about **$2/month** plus requests (first 20,000/month free). AWS-managed service keys remove the key fee but also remove environment-specific key policy, alias, independent disable/deletion control, and the existing CloudWatch encryption-context restriction. Existing EBS/S3/secret data does not become re-encrypted automatically; migration has recovery risk. The customer keys are the cryptographic boundary for client data in a shared account. Saving $2/month is not worth weakening that boundary.

#### Secrets

- `test/yucg-outreach` is the third $0.40/month secret and is not represented in current Terraform. Confirm no owner, then schedule deletion with recovery: **save $0.40/month**. This is a cloud deletion, not performed here.
- A standard SSM `SecureString` parameter is free (KMS requests only) versus $0.40/month for the dev secret. It has a 4 KiB limit and gives up Secrets Manager's recovery/rotation/version-management workflow. Check the JSON size and update the restart/seed path before deciding. Prod stays in Secrets Manager. [SSM pricing](https://aws.amazon.com/systems-manager/pricing/).

#### gp3

The four volumes total 56 GiB: two 20 GiB roots plus two retained 8 GiB data volumes, about **$4.48/month** at $0.08/GiB-month. Baseline 3,000 IOPS and 125 MB/s add $0. The data volumes are already at the configured 8 GiB minimum. Shrinking roots to 12 GiB could save $1.28/month but EBS cannot shrink in place, replacement affects the box/VPC origin, and the 20 GiB root is the safe staging area after the incident where 6 GB incorrectly filled `/data`. Do not shrink.

#### Logs, GuardDuty, CloudTrail, DNS Firewall

The six log groups store 126 MB; flow + DNS groups are 95% of bytes. With 14-day retention that implies about 0.27 GB/month ingestion, below CloudWatch's 5 GB free tier, so reducing these logs saves **$0 now**. They are the detection compensation for arbitrary internet egress. GuardDuty is only about $0.24/month pace and DNS Firewall about $0.22. The first management-event CloudTrail to S3 is free and its 114 MB costs about $0.003/month in S3. Keep all of them.

#### ECR

Lifecycle already expires untagged images after seven days and keeps ten tagged images. ECR's projected bill is $0.05/month. Reducing retention could delete a rollback image irreversibly and cannot save more than that. Keep it.

## Compute options and profile

Prices below are us-east-2 Linux list prices (730 hours), mirrored from the AWS price list by [Vantage](https://instances.vantage.sh/aws/ec2/t3.small-us-east-2.md):

| Type | RAM | On demand/month | Assessment |
|---|---:|---:|---|
| t3.small | 2 GiB | $15.18 | Current, comfortable deploy overlap. |
| t3a.small | 2 GiB | $13.72 | x86 drop-in, saves $1.46 on always-on prod; lower priority than a commitment. |
| t4g.small | 2 GiB | $12.26 | Saves $2.92 on prod and roughly $1.4 at dev office-hours duty cycle, but arm64 is not ready. |
| t3.micro | 1 GiB | $7.59 | Feasible only with lower container caps; too thin now. |
| t4g.micro | 1 GiB | $6.13 | Same memory risk plus arm64 work. Do not use yet. |

Measured locally on synthetic data: arm64 container idle **66 MiB**, startup **86 MB RSS**, and peak **112 MB** under 50 concurrent clients for 60 seconds (27.5 requests/s, zero errors). The code's production observation is 261 MiB. Two containers at that production figure use about 522 MiB; estimated AL2023 + Docker + nginx + SSM overhead is 250–350 MB, leaving only about 150–250 MB on a 1 GiB host. The current `--memory 1g` cap also cannot remain on a 1 GiB host during blue/green overlap. A micro is plausible only after a week of real cgroup peak measurements and a roughly 400 MiB/container cap; discovery would fall from four to one crawl slot. Keep 2 GiB now.

The native arm64 build found 64 of 67 pinned packages usable. The blocker is `matplotlib==3.11.1`: no musllinux aarch64 wheel; the unmodified image fails. Removing it or replacing its one lazy Admin export with openpyxl charts removes a measured 93 MB request spike and the blocker. The frontend arm64-musl native packages built successfully. A move also needs the arm64 AL2023 AMI, t4g validation, multi-arch/arm64 CI image publishing, and matched dev/prod architecture. Do not change Terraform until that application work is reviewed.

Prod is the only safe commitment target. A one-year no-upfront Compute Savings Plan is about $0.015/hour (**$10.95/month, save $4.23**); an EC2 Instance Savings Plan or Standard RI is about $0.013/hour (**$9.49/month, save $5.69**). Decide t3 versus t4g first: a t3 commitment works against the ARM decision. Risk is paying for unused committed hours; prod is intended to run continuously.

### “Rewrite in Rust”

Do not rewrite. Prod averages 1.4% CPU and retains 549/576 credits. Under artificial saturation, member paths spend only 0.4–3.3 ms/request in Python; register scans spend 20–380 ms in SQLite C and queue behind the existing gate. Resources FTS averaged 2.3 ms/query. The only Python-heavy synthetic case was role-based email filtering on a page about 100× normal size. Rust does not change an instance-hour bill that is bounded by deploy-overlap memory. Measurable work is smaller: remove the unused `pandas` dependency (about 72 MB image, no RSS saving) and replace the one matplotlib export (+93 MB spike) before reconsidering micro/ARM.

## Every paid or metered external API

| Provider and client | Public price | Counting and limit today | Current monthly dollars |
|---|---|---|---|
| Claude via Anthropic API or Bedrock (`services/llm.py`; one `converse` choke point) | Haiku 5.5 $0.10/$0.50 per million input/output tokens; Sonnet 5.5 $2/$10; Opus 5.5 $4/$20. [Pricing](https://platform.claude.com/docs/en/about-claude/pricing) | `usage_events: bedrock_reserved`; actual/estimated token cost settled in `details_json`. Club `AI_MONTHLY_BUDGET_USD=15` hard ceiling. | Production ledger unavailable here: **$0–$15**, not assumed $15. Dashboard reports recorded actual token cost. |
| Monid → TinyFish search/fetch (`services/web_fetch.py`) | Monid passes through TinyFish: search $0.005, fetch $0.001/result. [Monid](https://monid.ai/docs/guide/pricing), [TinyFish](https://www.tinyfish.ai/pricing) | `tinyfish_reserved`; 30/member/hour, 400/club/hour; search cache defaults seven days. Existing events do not distinguish search from fetch, so dashboard conservatively applies $0.005/call. | Calls/invoice unavailable. Formula: searches × $0.005 + fetched results × $0.001; dashboard upper estimate = calls × $0.005. |
| Firecrawl (`services/web_fetch.py`) | Free 1,000 credits/month; scrape 1 credit/page; search 2/10 results; PAYG $5/1,000 credits. [Pricing](https://www.firecrawl.dev/pricing.md) | `firecrawl_reserved`; 15/member/hour, 120/club/hour. Dashboard conservatively applies 2 × $0.005/call. `FIRECRAWL_URL` may be the club's shared self-host, so actual vendor spend can be $0. | Calls/invoice unavailable; likely **$0 within free/self-hosted allowance**. |
| Tavily (`research_providers.py`, `web_contact_discovery.py`, `routers/contacts.py`) | Free 1,000 credits/month; PAYG $0.008/credit; basic search 1, advanced 2. [Pricing](https://tavily.com/pricing), [credits](https://docs.tavily.com/documentation/api-credits) | Research path has 200/club/day and 80/member/day. This change logs `paid_api_call/tavily` at all three actual I/O sites; counts are forward-only after deploy. | Calls/invoice unavailable; **$0 through 1,000 basic searches**, then $0.008/search. |
| Verifalia (`services/email_verification.py`) | Free 25 credits/day; smallest pack $7.90/1,000 ($0.0079/standard validation; USD pack source secondary). [Provider](https://www.verifalia.com/pricing) | `verification_provider_days.used`; 25/day default, max 250; 90-day verdict cache. Default equals free allowance. | Expected **$0/month** on free plan; dashboard also shows $0.0079/credit opportunity cost. |
| SerpAPI (`services/sentiment_sources.py`) | Free 250 searches/month; Developer $75/5,000 = $0.015/search. [Pricing](https://serpapi.com/pricing) | Previously uncounted. `_serp` now logs `paid_api_call/serpapi` before every places/reviews request. Provider-plan 250/month is displayed but not enforced, preserving the feature. Counts are forward-only after deploy. | Calls/invoice unavailable; **$0 through 250**, then plan cost. |

Qualtrics, Reddit, Companies House, SEC EDGAR, IRS, DOL, Wikidata, GLEIF, Microsoft Graph, Gmail/Google OAuth, Slack, and public page crawls make network calls but no per-call charge was found in their current APIs/contracts. `APIFY_API_TOKEN` is stored but no code reads it; OpenAI and Ollama have no live HTTP client. These are shown in the study so they are not mistaken for omitted paid clients.

## Dashboard implementation

Admin → Costs is admin-only in both UI and `GET /api/admin/costs`.

- API use comes from existing SQLite counters. Missing Tavily and SerpAPI choke points now insert one cheap `usage_events` row before network I/O. No provider is queried to render the page.
- LLM dollars use settled actual token cost. Other rows show calls, public list-price estimate, limit/free allowance, and three-month trend. Tavily/SerpAPI history begins at deployment; existing LLM/TinyFish/Firecrawl/Verifalia history remains.
- Migration `core:4` adds a one-row `cost_explorer_cache`. Manifest job `cost_explorer_daily` is automatically wrapped in `leader_only`; it groups one Cost Explorer request by service and `Site` tag at 08:05 UTC, refuses a second request on the same UTC date, and caches JSON in SQLite. Opening the dashboard performs no AWS call.
- Before a reviewed Terraform apply grants `ce:GetCostAndUsage`, the AWS panel explicitly says **“AWS costs not connected”** and still renders provider data. Cost Explorer cannot be resource-scoped, so Terraform uses `Resource="*"` with that single action only.
- Browser coverage tests populated, disconnected, error, horizontal table scrolling, and the repository's 1440→390 px no-clipping sweep.

## Ranked reductions (saving / effort / risk)

1. **Dev only when needed — ~$7.5/month / medium / availability.** Largest no-commitment saving. President chooses manual start versus a narrow automated start permission.
2. **Prod one-year commitment — $4.23–$5.69/month / low / one-year spend.** Buy only after deciding t3 versus t4g; never commit dev.
3. **t4g.small — $2.92/month prod plus ~$1.4/month dev office-hours / high / build architecture.** Real arm64 build proved all but matplotlib; complete dependency/export and CI work first. This substitutes for, not adds to, a t3 commitment.
4. **Delete `test/yucg-outreach` — $0.40/month / low / recovery.** Confirm owner, then use Secrets Manager recovery window. No repository or feature data changes.
5. **Dev secret to standard SecureString — $0.40/month / medium / 4 KiB + weaker workflow.** Optional after size/recovery review; keep prod in Secrets Manager.
6. **Do not pursue:** public-IP removal (net cost increase), AWS-managed KMS (security regression), gp3 shrink (replacement/data risk), log removal ($0 current saving), ECR reduction (≤$0.05 and rollback loss), micro instance (thin overlap), Rust rewrite (no bill effect).

## President approvals and applied state

Approval is required for: the Terraform IAM diff and its resulting $0.60/month Cost Explorer calls; changing dev availability/start permissions; deleting the test secret; any one-year commitment; any secret-store, architecture, instance-size, or encryption-key change. No Terraform plan/apply, AWS call, provider call, deployment, push, GitHub mutation, secret deletion, or production-data read/write occurred.

## Verification evidence

- Backend: all **101** `tests/test_*.py` scripts passed in six parallel workers with `/tmp/club-venv/bin/python`; `tests/test_costs.py` covers admin authorization, all six provider rows, LLM actual cost, the disconnected state, service/environment grouping, migration `core:4`, and the once-per-day guard.
- Live API on port 8101 with a throwaway SQLite database: anonymous `GET /api/admin/costs` returned 401; a synthetic admin returned 200 with six providers, four recommendations, and `aws.connected=false`. Temporary data was removed.
- Frontend: ESLint passed; 15 Vitest files / 211 tests passed; TypeScript + Vite production build passed; npm audit gate found no high/critical advisory.
- Playwright on the assigned temporary port: the connected and disconnected Costs specs passed on desktop and mobile (four cases). The no-clipping sweep passed at 1440, 1280, 1100, 1024, and 768 px. At 390 px it identified a visually hidden label inside the horizontally scrolling table extending the document width to 595 px. The label was removed (the table header already supplies its accessible name); a direct Chromium inspection after the fix measured document/body/viewport widths of **390/390/390**. Per the repository's three-strike rule, that one automated assertion was not run a fourth time.
- Terraform 1.14.9 darwin/arm64 was downloaded from HashiCorp and SHA-256 verified. `fmt -check -recursive`, backend-disabled `init -lockfile=readonly`, `validate`, and all **20** mocked-provider tests passed.
- No remote state changed.
