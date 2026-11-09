# Terraform operator notes

Architecture and delivery rules live in [docs/CI_CD.md](../docs/CI_CD.md). This file holds the operator steps that the CI path cannot perform. All regional resources are in `us-east-2`.

## First provisioning of an environment (operator credentials, never CI)

1. `AWS_PROFILE=<operator> scripts/bootstrap-account.sh us-east-2` once per account: state bucket `yucgtfstate<account>`, GitHub OIDC provider, audit trail, GuardDuty. Dev and prod share account `073813807852`, which is already bootstrapped; skip this step there. Run steps 2–8 once per environment, each with its own state key.
2. Enable Bedrock model access for Anthropic Claude Haiku 4.5 in the account (first-use form).
3. Prod only, when `origin_read_timeout` is above 60: get a Service Quotas increase for the CloudFront origin response timeout first, or the distribution update fails.
4. Initialise and apply:

   ```sh
   terraform -chdir=terraform init -backend-config=bucket=yucgtfstate<account> \
     -backend-config=key=club-res-website/<env>/terraform.tfstate -backend-config=region=us-east-2 -backend-config=encrypt=true
   terraform -chdir=terraform apply -var environment=<env> -var manage_github_oidc_provider=false \
     -var tf_state_bucket=yucgtfstate<account> -var tf_state_key=club-res-website/<env>/terraform.tfstate \
     -var budget_email=<address> -var monthly_budget_usd=<amount>
   ```

   Prod waits about 15 minutes for the CloudFront VPC origin before it can add the box's port-80 rule.
5. `scripts/seed-secrets.sh <env>` stores the container secrets (JWT_SECRET is generated and kept).
6. Copy outputs into the GitHub environment `<env>`: `aws_region` → `AWS_REGION`, `aws_account_id` → `AWS_ACCOUNT_ID`, `deploy_role_arn` → `AWS_DEPLOY_ROLE_ARN`, `ecr_repository` → `ECR_REPOSITORY`, `instance_id` → `BOX_INSTANCE_ID`, `site_bucket` → `SITE_BUCKET`, `distribution_id` → `CLOUDFRONT_DISTRIBUTION_ID`, `site_url` → `SITE_URL` (the last three are empty in dev). `terraform_role_arn` goes to `infrastructure-<env>-plan` and `-apply`.
7. Google OAuth client: redirect URI `<site_url>/api/auth/google/callback` in prod, `http://localhost:8000/api/auth/google/callback` in dev.
8. The first container starts through the normal ship path (`ops/restart-yucg.sh` over SSM). The box's first boot only mounts `/data` and writes `/etc/yucg/config.json`.

Later changes go through the reviewed saved-plan workflow. The Terraform role updates existing resources only; anything that creates, replaces or deletes a resource runs with operator credentials again. Non-default settings must be set as `TF_<VARIABLE>` variables on both infrastructure environments (see `scripts/terraform-release.sh`), or the next plan reverts them.

## Operating the box

- Dev has no public listener. Reach the container with
  `aws ssm start-session --target <instance_id> --document-name AWS-StartPortForwardingSession --parameters portNumber=8000,localPortNumber=8000`, then open `http://localhost:8000`.
- On / off / status (volumes are kept; mail jobs run only while on):
  `aws ec2 start-instances --instance-ids <id>`, `aws ec2 stop-instances --instance-ids <id>`,
  `aws ec2 describe-instances --instance-ids <id> --query 'Reservations[0].Instances[0].State.Name'`.
  `office_hours_enabled=true` adds a schedule: start 12:00 UTC Monday–Friday, stop 04:00 UTC Tuesday–Saturday.
- Configuration changes are Terraform changes: the SSM parameter `/club-res-website/<env>/config` is rewritten and the next deploy copies it to the box. The first-boot script and AMI are ignored after creation, so Terraform never replaces the box by itself.
- Growing `data_volume_gb` grows the EBS volume only; run `sudo xfs_growfs /data` on the box afterwards.
- Self-healing: Docker restarts an app container that exits or is killed for exceeding its 1 GB memory cap, and the `yucg-watchdog` timer restarts the serving container once Docker marks it unhealthy (`/api/health` failing for about 90 s). Measured on dev 2026-11-09: a frozen process was serving again 231 s later. `journalctl -u yucg-watchdog` shows each restart.

## Alerts (prod)

`alerts.tf` emails `budget_email` from two SNS topics, `club-res-website-prod-alerts` in us-east-2 and in us-east-1 (CloudFront metrics live there). Each subscription must be confirmed once from its email. Alarms:

| Alarm | Fires when | Automatic action |
|---|---|---|
| `…-box-hardware` | AWS host check fails 2 minutes | EC2 recovers the box onto new hardware (same ID, IP and volumes) |
| `…-box-unresponsive` | Instance check fails 3 minutes | EC2 reboots the box |
| `…-app-errors` | 5 or more `ERROR:`/traceback log lines in 5 minutes | none; read CloudWatch `/club-res-website-prod/app` |
| `…-site-errors` | 25% or more of requests fail for 10 minutes (at least 3 requests per 5 minutes) | none |

Test only the notify-only alarms: `aws cloudwatch set-alarm-state --alarm-name club-res-website-prod-app-errors --state-value ALARM --state-reason test` (it returns to OK at the next evaluation). Never force the two box alarms: their actions run and would reboot or move prod.

Slack: authorize the workspace once under Amazon Q Developer in chat applications in the console, invite the app to the alert channel, then set `TF_SLACK_TEAM_ID` and `TF_SLACK_CHANNEL_ID` on both `infrastructure-prod-*` environments and apply a reviewed plan.

## Restoring the database

The verified path is the daily `.backup` set in the backups bucket (`sqlite/YYYY/MM/DD/<time>-…/`: `snapshot.db`, `restore-check.json`, `manifest.json` written last). At most a day of writes is lost (backups run 06:00 UTC); the five newest pre-deploy copies in `/data/backups/` are more recent after a release.

Drill (read-only for the live site; run monthly): send `ops/restore-drill.sh` through `aws ssm send-command … AWS-RunShellScript`. It restores the newest set to a scratch directory, checks it against its manifest and live row counts, and boots the serving image on it without networking. 2026-11-09 on prod: 243 MB set, checksum and integrity ok, 93 of 94 tables identical to live, app healthy, 10.9 s total.

Real restore (prod is down from step 3 to step 6; announce it):

1. Run the drill commands to download and verify the chosen set into a scratch directory on the box.
2. `exec 9>/var/lock/yucg-deploy.lock; flock 9` in the root shell, so neither a deploy nor the watchdog acts.
3. `docker stop -t 60 yucg-$(cat /etc/yucg/active-slot)`.
4. Keep the current files: `mkdir /data/backups/pre-restore-$(date -u +%Y%m%dT%H%M%SZ)` and move `/data/clientreach.db`, `-wal` and `-shm` into it.
5. `install -o 10001 -g 10001 -m 0600 <scratch>/snapshot.db /data/clientreach.db`.
6. `docker start yucg-$(cat /etc/yucg/active-slot)`, then check `curl -s http://127.0.0.1/api/health` and a known row count; release the lock by closing the shell.

The daily EBS snapshots (7 kept) are a second, crash-consistent copy; restoring one means a new volume from the snapshot and is not drilled.

## Replacing the box (operator credentials; the data volume is kept and reattached)

Dev: `terraform apply -replace=aws_instance.box …`, then update `BOX_INSTANCE_ID` and redeploy.

Prod: the CloudFront VPC origin points at the instance, and CloudFront refuses to change a VPC origin that a distribution uses (`CannotUpdateEntityWhileInUse`). `/api` is down from step 3 until step 5 finishes; announce it.

1. Run a backup on the box: `sudo systemctl start yucg-backup.service`.
2. Lift termination protection: `aws ec2 modify-instance-attribute --instance-id <id> --no-disable-api-termination`.
3. Remove the API origin and both `/api` behaviors from the distribution, then wait:
   `aws cloudfront get-distribution-config --id <dist> > dc.json`
   `jq '.DistributionConfig | .Origins.Items |= map(select(.Id != "box-api")) | .Origins.Quantity = (.Origins.Items | length) | .CacheBehaviors.Items |= map(select(.TargetOriginId != "box-api")) | .CacheBehaviors.Quantity = (.CacheBehaviors.Items | length)' dc.json > new.json`
   `aws cloudfront update-distribution --id <dist> --if-match "$(jq -r .ETag dc.json)" --distribution-config file://new.json && aws cloudfront wait distribution-deployed --id <dist>`
4. `terraform apply -replace=aws_instance.box …`: it replaces the box (re-enabling termination protection), moves the data volume, updates the now-unused VPC origin, and restores the API origin and behaviors that step 3 removed.
5. `aws cloudfront wait distribution-deployed --id <dist>`, update `BOX_INSTANCE_ID`, and deploy through the ship path; the new box runs no container until then.

## Network limits

- Outbound HTTP(S) is open to the internet: the app crawls arbitrary company sites and checks MX records for arbitrary recipient domains, so a domain allowlist cannot work. DNS Firewall blocks only the AWS-managed malware and botnet lists; DNS must use the VPC resolver, and every query is logged for 14 days.
- The S3 endpoint policy covers in-region S3 only. Catalog and backup objects are reachable only through this VPC's endpoint, except for roles listed in `catalog_operator_principal_arns` (workbook upload, backup restore). Document objects are reachable from browsers, but only through presigned URLs signed by the box role (CORS allows only the app's origin).
- The DNS Firewall list IDs in `network.tf` are the AWS-owned IDs for `us-east-2`; check them with `aws route53resolver list-firewall-domain-lists --region us-east-2`.
