# Cost reductions, round 2

Date: 2026-11-17. This is the follow-up to [`costs.md`](costs.md), not a second billing audit. The AWS baseline and resource facts are the same supplied read-only snapshot collected 2026-11-17 03:54 UTC (`/tmp/aws-cost-facts.md`): **$29.07/month November pace**. No AWS, GitHub, provider, production-data, Terraform plan/apply, push or deployment call was made for this report.

## Decision summary

This branch implements the two changes that are safe before an infrastructure apply:

1. **Dev runs only for a release.** Its deploy job starts the box, waits for EC2 `running` and a fresh SSM `Online` ping, deploys and runs the existing on-box health check, then stops it in an `always()` step. A non-cancelling `club-res-website-dev-box` concurrency group prevents one release stopping another. The office-hours scheduler is removed. The dev deploy role gets only its one instance's start/stop, the EBS grant required to start its encrypted volumes, and read-only EC2/SSM boot status; prod gets none of them. Mocked Terraform tests cover both roles and both boundaries. This code is committed but not applied.
2. **Unused `pandas` is removed.** The exact amd64 image content size fell from 167.6 MB to 147.3 MB; idle RSS stayed effectively flat (86.64 to 86.09 MiB). This is maintainability/image-transfer work, not a material AWS bill reduction because ECR deduplicates layers and costs $0.10/GB-month.

Using the first study's verified **about $7.5/month** dev compute + public-IPv4 saving, the branch's post-apply AWS pace is **about $21.9–$22.3/month**: $29.07 − $7.5 + about $0.3–$0.7 for the already-built Cost Explorer dashboard after dev's daily reader is usually off. The range allows release/review runtime and tax. It is an evidence-bounded model, not an invoice: the first full month after apply is the only way to verify stop reliability, release hours and tag allocation together. Deleting the abandoned secret would lower that to **about $21.5–$21.9/month**. A commitment is deliberately excluded until the president chooses t3a versus ARM.

## Ranked decisions

Savings are non-overlapping only where stated. A commitment and instance-family move are alternatives, not additions to every other row. “Apply” includes an AWS or GitHub setting mutation where Terraform alone cannot do it.

| Rank | Measure | Estimated saving / month | Effort | Risk / reversibility | Needs apply or AWS action? | Implemented in this branch? |
|---:|---|---:|---|---|---|---|
| 1 | Run dev only for releases; remove office hours | **~$7.5** versus the current schedule | Medium | Low/medium: dev, its jobs and SSM reviews are offline; reversible by reverting code/apply. Prod unchanged. | **Yes:** reviewed operator apply deletes two schedules and their role, changes IAM/boundaries; then stop today's running dev box once. | **Yes, code only** (`1da767d`) |
| 2 | Move prod to `t3a.small`, then buy 1-year no-upfront commitment | **$5.26** with Compute Savings Plan (`$0.0136/h`) or **$6.57** with EC2 Instance Savings Plan/Standard RI (`$0.0118/h`) versus current t3.small on demand; includes the **$1.46** t3a on-demand saving | Low/medium | Brief prod stop/start; t3a availability/capacity in `us-east-2a` must be checked before apply. Compute plan is less saving but preserves an ARM move; one-year purchase is not reversible. | **Yes:** set `TF_INSTANCE_TYPE=t3a.small`, reviewed apply and live deploy; separate Savings Plan/RI purchase. | No; recommended after one live peak-memory check |
| 3 | ARM `t4g.small` plus 1-year instance commitment | **$7.52** versus t3.small on demand (`$0.0105/h`), but alternative to rank 2 | High | Architecture migration; reversible before purchase, not after family-specific commitment. Needs matplotlib replacement, arm64 lock, arm64 AL2023 AMI, image publishing and live validation. | Yes: application/CI work, replacement/migration plan, Terraform apply, then purchase | No; measured path below |
| 4 | Shrink dev volumes during its next rebuild: root 20→10 GiB and data 8→1–2 GiB | **$1.28–$1.36** at $0.08/GiB-month | Medium/high | EBS cannot shrink in place; copy synthetic/approved data, verify restore, replace dev box/volume and update `BOX_INSTANCE_ID`. Root floor must be confirmed from the selected AMI block mapping; 10 GiB leaves deploy headroom above an expected 8 GiB AMI snapshot. Reversible only via snapshot/new volume. | Yes: read-only AMI/`df` check, operator migration/apply, GitHub binding update | No; not safe blind |
| 5 | Schedule deletion of abandoned `test/yucg-outreach` secret after ownership/use check | **$0.40** | Low | 30-day recovery window; reversible only during that window. It is not in Terraform and no current code names it. | **Yes:** CloudTrail/owner check, then Secrets Manager deletion | No; no AWS credentials |
| 6 | Move only the dev app secret to standard SSM `SecureString` | **$0.40** | Medium | 4 KiB limit; gives up Secrets Manager recovery/rotation workflow and makes dev stop testing prod's secret path. Reversible, but migration can break deploy. | Yes: size/use check, code + IAM + seed migration, apply, later secret deletion | No; **not recommended** for $0.40 |
| 7 | Replace matplotlib Admin PNG exports with openpyxl charts; unlock ARM | **≤$0.01** direct ECR, **$0 idle RSS**; enables rank 3 and avoids the previously measured 93 MB export spike | Medium | Export fidelity/regression risk; reversible in code. | No AWS apply until the separate ARM cutover | No; preserve the current feature until replacement tests exist |
| 8 | Remove unused pandas | **≤$0.004** after both repositories churn through their 10-image retention | Low | Low and reversible. 20.4 MB compressed image reduction; no idle RSS reduction. | No | **Yes** (`2bbe3a0`) |
| 9 | Reduce ECR retention below 10 tagged / 7-day untagged | **≤$0.05 total current ECR bill** | Low | Deletes rollback images and cannot be undone; existing lifecycle is already lean. | Terraform apply | No; **do not pursue** |
| 10 | Reduce dev DLM snapshots below seven daily snapshots | **≤$0.08 account-wide observed snapshot pace**; dev subset smaller | Low | Removes an independent recovery path; stopped-volume snapshots are incremental and mostly unchanged. | Terraform apply | No; **keep** |
| 11 | Shorten/drop VPC flow, DNS query or app logs | **$0 now** | Low | Weakens detection evidence; all six groups total only 126 MB at 14 days and observed CloudWatch spend is $0. The 5 GB monthly Logs allowance covers this scale; dev volume also falls when stopped. | Terraform apply | No; **keep** |
| 12 | Disable GuardDuty or DNS Firewall “in dev” | At most the current **$0.24 GuardDuty + $0.22 DNS Firewall account pace**, and less after dev stops | Medium | Security regression for arbitrary internet egress. GuardDuty is one account-level detector, not a dev resource; DNS Firewall blocks managed threat lists and its query charge naturally falls while dev is stopped. | Bootstrap/Terraform mutation | No; **keep** |
| 13 | Use an AWS-managed key for dev | **$1 now** (more after KMS key rotations) | High | Loses environment key policy, independent disable/deletion control and CloudWatch encryption-context restriction; existing EBS/S3/secret data must be re-encrypted. Dev can hold limited real client data when a feature requires it. | Destructive migration/apply | No; **do not weaken encryption** |
| 14 | Shorten S3 backups or document noncurrent-version retention | **< $0.02** at current 728 MB prod backups and empty measured documents bucket | Low | Backups are current for 30 days, then can remain noncurrent for up to 30 more; documents deliberately retain noncurrent versions for 30 days. Shortening loses recovery for cents. | Terraform apply | No; **keep** |
| 15 | Change CloudFront or remove prod public IPv4 | **$0 CloudFront observed**; removing IPv4 is a **net cost increase** | High | CloudFront already uses `PriceClass_100` and recorded no November charge. Prod needs arbitrary IPv4 egress; one Ohio NAT gateway is $32.85/month plus data versus $3.65/month public IPv4. | Major network apply | No; **keep** |
| 16 | Shrink prod's 20 GiB root volume | Up to **$0.80** at 10 GiB | High | Prod staged a 6 GB import on root after `/data` filled; usage was not available. EBS cannot shrink. Never trade recovery/deploy headroom for an unmeasured saving. | Read-only `df`, then replacement migration/apply if justified | No; **not safe now** |

## Measurements and findings the first study did not have

### Release-only dev

The scheduled app jobs do not justify an office-hours box:

- Dev has `email_delivery_enabled=false`; mail and Gmail jobs never send there.
- The 06:00 UTC SQLite timer and DLM snapshots protect synthetic/approved dev state but are not production records. The systemd timer is persistent and runs after the next boot; DLM snapshots do not require the instance running.
- The 08:05 Cost Explorer cache may be stale while dev is off. Prod still refreshes the same account costs daily, and an Admin cost page on a stopped private dev box is unreachable anyway.
- SSM port-forward reviews are explicit operator sessions: start, wait for SSM, review, stop. A release finishing during one also stops the box; the reviewer must coordinate with the serialized deploy.

A stopped instance does not charge compute or an attached ephemeral public IPv4, but all four EBS volumes remain billed. The workflow therefore removes the variable cost, not the fixed dev storage/key/secret cost.

### Storage and retention

Terraform matches the supplied facts: each environment has a 20 GiB gp3 root plus retained 8 GiB gp3 data volume, all at baseline 3,000 IOPS/125 MB/s. Fifty-six GiB is about **$4.48/month** at the Ohio-like $0.08/GiB-month price used by the bill; stopping does not change it. AWS states that gp3 is charged on provisioned GiB until release and baseline performance is included ([EBS pricing](https://aws.amazon.com/ebs/pricing/)).

The account used only 1.637 GB-month of EBS snapshots through Nov 17 ($0.082); seven incremental daily snapshots per volume are not a useful cut. Prod S3 backups were 728 MB, dev 20 MB. The current rules retain current backup objects for 30 days, all noncurrent bucket versions for 30 days, and document current versions indefinitely. That is the recovery contract, not forgotten storage.

### Keys and secrets

AWS charges **$1/customer-managed KMS key-month**, and the first and second automatic rotations add another $1/month each; AWS-managed keys have no storage fee ([KMS pricing](https://aws.amazon.com/kms/pricing/)). The two young environment keys explain about $2/month now. Rotation means the long-run key line could become $4/month after the first annual rotation and $6/month after the second unless AWS pricing changes. Do not disable prod rotation or replace its key for this saving. Dev's key has the same security/re-encryption trade-off and stays too.

Secrets Manager charges **$0.40/secret-month** ([Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/)); standard Parameter Store has no additional charge ([Systems Manager pricing](https://aws.amazon.com/systems-manager/pricing/#Parameter_Store)). Keep the two real app secrets on one tested path. Delete only `test/yucg-outreach`, after proving no owner or recent read.

### Logs, GuardDuty, DNS Firewall, ECR and edge

The 14-day app/flow/DNS groups store 126 MB total. CloudWatch includes 5 GB/month across ingestion, archive storage and Logs Insights scan ([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)); the supplied bill has no CloudWatch line. VPC Resolver query logging itself is free; its destination can charge ([Route 53 pricing](https://aws.amazon.com/route53/pricing/#Resolver)). DNS Firewall is $0.60/million foundational queries and the measured 206,072 queries cost $0.124. Keep both logs and filtering.

Private ECR storage is $0.10/GB-month and same-region EC2 pulls are free ([ECR pricing](https://aws.amazon.com/ecr/pricing/)). The measured ECR bill is $0.05. Removing pandas cuts a new compressed image by 20.4 MB, so even after both repositories' retained images turn over the maximum direct saving is below half a cent because layers deduplicate within each repository. The rollback value of ten releases is greater.

CloudFront is already `PriceClass_100`; there was no CloudFront November line. The prod box's $0.005/hour public IPv4 is cheaper than a $0.045/hour Ohio NAT gateway, before the NAT's $0.045/GB processing ([VPC pricing](https://aws.amazon.com/vpc/pricing/)). Keep both.

### Compute and image profile

Measured in this round on the same arm64 Mac using amd64 emulation:

| Image | `docker image inspect .Size` | Idle RSS after `/api/health` |
|---|---:|---:|
| Existing requirements | 167,639,445 bytes | 86.64 MiB |
| Without unused pandas (committed) | 147,262,517 bytes | 86.09 MiB |
| Without pandas or matplotlib (throwaway only) | 97,021,160 bytes | 86 MiB |

Only `backend/app/routers/operations.py` imports matplotlib, lazily, for two Admin PNG exports and their full-cache ZIP. A native arm64 image without both packages built to 96,432,362 bytes **only after** compiling a separate `aarch64-manylinux_2_28` lock; the unchanged x86 lock correctly failed hash/wheel resolution. ARM therefore requires a reviewed lock/CI/AMI cutover, not just a Terraform instance-type edit.

Current Ohio Linux rates and 730-hour costs:

| Type / purchase | Rate | Monthly compute | Saving vs t3.small on demand |
|---|---:|---:|---:|
| t3.small on demand | $0.0208/h | $15.18 | — |
| t3a.small on demand | $0.0188/h | $13.72 | $1.46 |
| t3a.small, 1-year no-upfront Compute Savings Plan | $0.0136/h | $9.93 | $5.26 |
| t3a.small, 1-year no-upfront Instance Savings Plan / Standard RI | $0.0118/h | $8.61 | $6.57 |
| t4g.small on demand | $0.0168/h | $12.26 | $2.92 |
| t4g.small, 1-year no-upfront instance commitment | $0.0105/h | $7.67 | $7.52 |

Exact rates are mirrored from the AWS price list by Vantage ([t3.small](https://instances.vantage.sh/aws/ec2/t3.small-us-east-2.md), [t3a.small](https://instances.vantage.sh/aws/ec2/t3a.small-us-east-2.md), [t4g.small](https://instances.vantage.sh/aws/ec2/t4g.small-us-east-2.md)); purchase terms are described on [AWS Savings Plans pricing](https://aws.amazon.com/savingsplans/pricing/) and [EC2 Reserved Instances pricing](https://aws.amazon.com/ec2/pricing/reserved-instances/pricing/). Do not buy a family-specific commitment before choosing t3a versus ARM. The reversible recommendation is t3a on demand first, observe one deploy overlap and one week of cgroup peaks, then choose the one-year Compute plan if ARM remains plausible.

## Exact president approval list

Nothing below happened. Approve each numbered mutation separately; an approval of this document is not an apply authorization.

1. **Dev releases-only Terraform applies (required for the implemented feature).** Remove `TF_OFFICE_HOURS_ENABLED` from both infrastructure-dev GitHub environments. Generate and review two saved plans at this commit: the **dev** plan deletes two EventBridge Scheduler schedules and their scheduler role/policy, adds the dev deploy policy and updates the dev boundary; the **prod** plan updates only the prod boundary so it still permits Terraform's existing start/stop of the prod box but denies every other instance. Because the dev plan deletes resources, run that apply with operator credentials under the repository's infrastructure gate, not the update-only Terraform role. The prod deploy role gains no action.
2. **One-time dev stop after the dev apply.** Stop only `i-03992481f6d6a9372` (`Name`/`Site=club-res-website-dev`) and wait for `stopped`; otherwise it remains running until the next Dev release's cleanup. Do not stop prod.
3. **Cost Explorer IAM already built in the parent work.** In those same two saved-plan reviews, approve each box role's single `ce:GetCostAndUsage` action and its expected roughly $0.30/month prod plus release-day dev requests. This is not a broad billing permission.
4. **Abandoned secret deletion.** First inspect CloudTrail/owner evidence for `test/yucg-outreach`; if unused, schedule only that secret for deletion with a 30-day recovery window. Do not delete either `club-res-website/<env>/app` secret.
5. **Choose one prod compute path, then apply it:** (a) set `TF_INSTANCE_TYPE=t3a.small` in both prod infrastructure environments after confirming `us-east-2a` offering/capacity, review the stop/start plan, apply and complete the live deployment checks; or (b) fund the full matplotlib/arm64 lock/image/AMI migration and review its replacement/data-volume attachment plan. Do not authorize both paths in one plan.
6. **Only after path 5 is healthy, purchase one commitment:** recommended one-year no-upfront **Compute Savings Plan at $0.0136/hour** if ARM remains a near-term option; otherwise the t3a one-year no-upfront **Instance Savings Plan/Standard RI at $0.0118/hour**. Cover prod only. Never commit dev. Record purchaser, term, hourly commitment, start/end dates and renewal owner.
7. **Optional dev storage rebuild, separate change.** Approve a read-only AMI block-map and `df -h / /data` check, snapshots/restore drill, then a 10 GiB root and 1–2 GiB retained data-volume migration only if the measurements fit. Review the exact old/new volume and instance IDs and update `BOX_INSTANCE_ID`. Do not shrink prod.

Not requested for approval: AWS-managed KMS migration, prod volume shrink, shorter log/snapshot/ECR/S3 retention, public-IP removal, GuardDuty/DNS Firewall removal, or dev secret-store divergence. Their savings do not justify their security, recovery or availability cost.

## Verification evidence

- Terraform 1.14.9: recursive format check, backend-disabled initialization, validation and **20/20 mocked-provider tests passed**; mutation copies proved the new scoped-policy assertions fail for a wildcard box ARN or wrong boundary tag.
- Workflow: `actionlint 1.7.12` passed.
- Repository: `python3 -m unittest discover -s tests -p 'test_*.py'` passed **59 tests**, including nine mocked start/stop/EC2/SSM state-machine cases.
- Dependency: regenerated the x86_64 hash lock with its header command; a clean Python 3.12 environment passed `backend/scripts/test_suite.py`; `trivy image --severity HIGH,CRITICAL --exit-code 1` found no high/critical issue in the pandas-free image.
- No remote state changed.
