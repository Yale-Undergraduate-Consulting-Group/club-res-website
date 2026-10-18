# AWS wiring and cost audit — September 9, 2026

Read-only inspection using the existing `andreheidvscode` AWS CLI profile. STS
identified the profile as the account root principal, not an IAM user. No AWS
resources were created, changed, stopped, or deleted. No secret values were read,
no SSM commands were sent, and no mail was sent. Two Cost Explorer queries were
made (standard primary-view API pricing is $0.01 per request).

Scope: YUCG in us-east-1, account-wide CloudFront/S3 listing and billing by service
and region. This is not an exhaustive inventory of every service in every region.
Other projects found in the account were not treated as YUCG cleanup targets.

## Verified live wiring

- `YucgOutreach-dev`: UPDATE_ROLLBACK_COMPLETE.
- Live app: https://d2vjpur8zjk0ai.cloudfront.net . Distribution E35QVGFDWHVOPG is
  enabled/deployed, routes through a VPC origin to the existing EC2 private address,
  uses caching-disabled policy, and enables compression.
- Live origin read timeout is **120 seconds**; local CDK says **60 seconds**.
  Earlier claims of a 60-second live timeout were based on local code, not AWS.
- EC2 `i-09a071e22270b027c`: running t3.small, launched September 8; SSM online.
- Attached storage: 20 GB root gp3 and 8 GB data gp3. The data attachment has
  DeleteOnTermination=false. The actual guest filesystem mount was not inspected.
- One automatically assigned public IPv4; no allocated Elastic IPs returned.
- No NAT gateways, load balancers, RDS instances/clusters, CodeBuild projects,
  CodePipeline pipelines, or Amplify apps returned in us-east-1.
- Existing GitHub OIDC stack is UPDATE_COMPLETE. Repository shipping path remains
  GitHub Actions -> ECR -> SSM container replacement; it is not a CDK app redeploy.
- No YUCG office-hours/idle-stop EventBridge rules were returned. CPU-credit alarm
  is OK and has no alarm actions.

## Actual costs and utilization

The Cost Explorer period September 1–8 (end date September 9 is exclusive) shows
roughly $0.47 of reported usage charges offset by credits. The period is estimated,
billing can lag, and this is an account-level view rather than a fully allocated
YUCG bill. Current-day September 9 usage is excluded. This does not establish
remaining credits, their expiration, or permanent free-tier eligibility.

Seventeen hourly EC2 CPU samples average about 0.71%; highest hourly average is
2.76%. No surplus CPU credits were charged in these samples. Instance credit mode
is unlimited. Host memory and application peak memory were not measured. Do not
rightsize based on less than a day of mostly idle CPU data.

## Concrete findings and proposed actions (not executed)

1. **Five unattached retained data volumes**, each 8 GB gp3, with tags pointing to
   older incarnations of YucgOutreach-dev:
   - vol-06208d1db995c031d
   - vol-0eeee26c3ba52de6f
   - vol-0c5f9a1fdde88a3e7
   - vol-01454d0c00cfd46d6
   - vol-02d723bc88964974b

   They total 40 GB, approximately $3.20/month at $0.08/GB-month before credits.
   Unattached does not mean disposable. Establish data ownership/recovery needs
   and identify the authoritative database before proposing specific deletions.
   Creating recovery snapshots would also require approval and incur storage cost.

2. **Replacement-sensitive stack updates have already failed.** Stack events show
   the data disk was already attached and CloudFront refused to update a VPC
   origin associated with a distribution. Stabilize the infrastructure deployment
   approach separately; keep app changes on the existing image/SSM path. Do not
   blindly redeploy the app stack to clear the status.

3. **ECR retention does not cover tagged deployment images.** The CDK repository
   has 24 image records. Their reported sizes sum to 4.30 GB, which is NOT unique
   billable storage because layers can be shared. Current lifecycle policy only
   expires untagged images after 365 days. Propose bounded tagged-image retention
   after identifying the active image and rollback versions; do not prune blindly.

4. **Two old pipeline artifact buckets remain:**
   - yucgpipeline-dev-pipeartifactsbuckete3de2035-0r35vvxlitun
   - yucgpipeline-dev-pipeartifactsbuckete3de2035-q4hyxmgswnbi

   Neither has a lifecycle policy. Their sizes and contents were not inventoried;
   savings cannot yet be quantified. Confirm they are no longer referenced before
   proposing retention/deletion.

5. **Catalog lifecycle rules expire current exports/discovery after 30 days but
   omit noncurrent-version expiration.** On a versioned bucket this need not remove
   old object versions. Propose explicit version retention after defining recovery
   needs. Main API log retention is already 14 days; several old helper/build log
   groups have no retention, but observed stored bytes were tiny.

6. **No current backup objects returned under catalog `exports/`.** This is not
   proof that no backups exist elsewhere. Verify the guest mount and backup method,
   then prepare SQLite online-backup + restore validation before disk cleanup.
   Directly copying only the live .db file can omit WAL transactions.

7. **Keep tracking available.** A stopped EC2 instance cannot record open pixels.
   Gmail reply/bounce synchronization can catch up afterward, but missed image
   requests cannot reliably be reconstructed. Do not enable idle shutdown merely
   because CPU is low if continuous tracking is required.

8. **AWS identity and deployment scope.** The local profile is root. Move normal
   operation to an appropriately scoped identity. Local GitHub ship-role code also
   grants infrastructure creation permissions far beyond image shipping; deployed
   role policies were not inspected. Prepare narrower permissions before applying
   any IAM changes.

## Remaining application work

- Review the full shared working tree and ship through the existing authorized
  GitHub deployment path. Local tracking/UI changes have not been deployed here.
- Test sent/open/reply/automatic-reply/delayed/permanent-failure flows with explicitly
  approved controlled mailboxes through the real CloudFront URL.
- Verify deployed Google read scopes, actual runtime URL settings, persistent EBS
  mount, and active image version without exposing credentials.
- Test follow-up suppression after replies/failures and recovery from interrupted
  sends; local receipt tracking tests do not establish exactly-once email delivery.
- Finish visual/browser QA for desktop/mobile and status/report consistency.
- Measure memory and CPU during a representative club workload before considering
  a smaller or different instance. Do not add NAT, RDS, ALB, or new workers for the
  current tracking requirement.

## Sources for cost/retention interpretation

- AWS Cost Explorer API pricing: https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/
- AWS EBS volume pricing: https://aws.amazon.com/ebs/volume-types/
- AWS S3 lifecycle behavior: https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html
- AWS burstable-instance credit behavior: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode-concepts.html
