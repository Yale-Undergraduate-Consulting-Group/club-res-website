# Claude Team and API: decision and cutover

## Recommendation

Move production from the temporary personal key to a **workspace-scoped service-account key in a club-owned Claude Console organization**, and link the existing Claude Team plan to that Console organization. Keep `AI_MONTHLY_BUDGET_USD=15`, set the Console workspace's monthly spend limit to the same **$15**, leave purchased-credit auto-reload off, and store only the inference key in `club-res-website/prod/app`. Do not put it in dev, the repository, GitHub, or a person's account.

This is the least-cost immediate route. Team now contributes pooled Claude Platform credits: **$20/month per Standard seat or $100/month per Premium seat, capped at $500/month**, including discounted Team plans. No Console payment method is required to use those credits. At even one Standard seat, the current $15 application ceiling fits inside the included credit; the Team plan itself requires at least two seats. Thus the expected incremental monthly charge is **$0**, provided auto-reload remains off and the linked credit is not consumed by other Console workspaces. The Team subscription is already a club cost, not a new service. Sources: [Team plan inclusions and price](https://support.claude.com/en/articles/9266767-what-is-the-team-plan) and [monthly API credits](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers).

Use:

- `claude-haiku-5-5` for ranking, extraction, and high-volume review: $0.10/million input tokens and $0.50/million output tokens up to a 100,000-token prompt (higher long-prompt tier after that).
- `claude-sonnet-5-5` for writing, Slack, and the current deep tier: $2/million input and $10/million output.
- Keep Opus off the direct path. Sonnet is the intentional deep-tier substitute and preserves the $15 allowance.

Those are the current first-party prices in [Anthropic's pricing table](https://platform.claude.com/docs/en/about-claude/pricing). The app's existing eight-call concurrency is far below the documented Start-tier limits for both models (4,000 RPM, 2,000,000 input tokens/minute and 400,000 output tokens/minute). Limits are organization-wide token buckets and short bursts or ramp limits can still return 429. Included Team credits move the linked organization to at least Start; a monthly credit over $200 moves it to at least Build. Source: [rate and spend limits](https://platform.claude.com/docs/en/api/rate-limits).

## What Team does and does not provide

Claude Team is the hosted Claude chat/work product. It includes central administration, role-based permissions, SSO/domain capture/JIT, spend controls, connectors, and the monthly API-credit pool. Its member chat limits remain per-user. [Team plan details](https://support.claude.com/en/articles/9266767-what-is-the-team-plan).

**Team is not itself the API organization.** Claude chat plans and Claude Console are separate products. A Team Owner or Primary Owner must create or choose a separate Console organization and link it at **Claude > Organization settings > Billing > API credits**. One Team plan can link to one Console organization and one Console organization can receive credits from one plan; changing that link later requires Anthropic Support. [Anthropic's separation explanation](https://support.claude.com/en/articles/9876003-why-is-claude-api-usage-billed-separately-from-my-paid-claude-plan) and [claim flow](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans).

All workspaces and API keys in the linked Console organization draw from the pooled credit. Create a non-default `Production` workspace because the Default Workspace cannot have a spend limit. A workspace can have a monthly spend cap plus model-specific RPM/input/output token caps, and a key can be scoped to exactly one workspace. [Workspace limits and keys](https://platform.claude.com/docs/en/manage-claude/workspaces).

### Ownership and SSO

A production key must be a **service-account key**, not a personal key. A personal key stops when its person loses organization access. A service-account key acts as a non-human identity and continues if the creating officer leaves; it stops only if the service account is archived or removed from the workspace. Create it with an explicit expiry and rotate it. [Key types and expiration](https://platform.claude.com/docs/en/manage-claude/authentication).

The Console organization still needs human stewards: appoint at least two current elected officer roles (recommended: President as Owner and Technology/Operations officer as Admin or Billing), using individual accounts and no shared login. Record role handoff in the officer transition checklist. The legal click-through must be made by someone authorized to bind the club; Anthropic's [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) say a person may not accept on an organization's behalf without that authority.

SSO is available for Team and Console organizations, but Console SSO setup requires an Admin, control of the email domain's DNS, and access to the club's IdP. The Console Identity page may require Anthropic to enable SSO or a merge proposal. Do not delay the key cutover for SSO: first establish two individual administrators and MFA; then configure shared SSO only if the club can verify a domain and Yale permits the IdP integration. The owner of `yaleconsulting.org` DNS and the club's ability to use a Yale IdP are currently unknown. [Anthropic SSO setup and prerequisites](https://support.claude.com/en/articles/13132885-set-up-single-sign-on-sso).

## Data protection and retention

The direct API is a commercial service. Anthropic's [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) say the customer retains inputs, owns outputs, and Anthropic may not train models on Customer Content. The [Privacy Center training article](https://privacy.anthropic.com/en/articles/7996868-is-my-data-used-for-model-training) likewise says commercial inputs and outputs are not used for training by default unless the customer deliberately submits feedback or opts in.

Under the standard commercial policy, API inputs and outputs are automatically deleted from Anthropic's back end within 30 days, except longer-lived features, legal requirements, and content flagged for Usage Policy enforcement. The newer API retention documentation says ordinary Messages content is not retained by default, but the 30-day policy is the conservative external commitment. Sources: [Privacy Center commercial retention](https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-personal-data) and [API retention feature matrix](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention).

Zero Data Retention is **not included merely because the club has Team**. Anthropic must approve and enable ZDR separately for each Console organization through sales. Eligible ordinary Messages calls then have no prompt/response stored at rest after the response, subject to flagged-content/legal-hold exceptions; Team and Console interfaces are not covered. The app calls Messages from its own back end and uses no Files, Batch, Managed Agents, or code-execution features, so its present request shape is ZDR-eligible if Anthropic approves it. Request ZDR, but do not describe the app as ZDR until the Console organization shows it enabled and the contract confirms the scope. [Anthropic API/ZDR scope](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) and [Privacy Center ZDR article](https://privacy.anthropic.com/en/articles/8956058-i-have-a-zero-retention-agreement-with-anthropic-what-products-does-it-apply-to).

Commercial provider protections do not make client data appropriate for every prompt. Continue the application's existing minimization and authorization rules. The local usage ledger stores model, member, purpose, token counts, and cost only—not prompts or responses.

## Spend and usage reporting

The application already reserves every call against the club's monthly budget before sending it and replaces the estimate with provider token counts and list-price cost after a successful response. Both Bedrock and first-party calls use the same `usage_events` ledger, so the Costs dashboard can show current-month calls and spend by model without another credential or network call.

A linked **Console** organization can also use the beta Usage and Cost Analytics API. A Team organization alone cannot. `GET /v1/organizations/analytics/cost` can filter by workspace/model/key and reports USD amounts after discounts but **before promotional credits**, in decimal cents. Data normally refreshes within four hours and can lag up to 24 hours. It measures credit consumption/list cost, not cash charged after the Team credit. The remaining promotional-credit balance still needs a human check on Console > Billing. [Usage and Cost Analytics API](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api).

For a Console organization, that API requires an Admin API key. Console Admin keys have broad organization access rather than a read-only analytics scope. [Admin API key permissions](https://platform.claude.com/docs/en/manage-claude/admin-api-keys). Therefore:

- default: leave `ANTHROPIC_ADMIN_API_KEY` unset and use the local ledger;
- optional reconciliation only: store a separate short-lived Admin key in the prod app secret, set `ANTHROPIC_COST_WORKSPACE_ID` to the Production workspace, and rotate it frequently;
- never reuse the inference key or expose either key to the browser.

The code now provides this optional read path in `app.services.ai_spend.anthropic_cost_report()`. It is disabled when the setting is absent, never affects inference, returns no secret, and degrades to an unavailable status rather than breaking the Costs page.

## Comparison with the two current alternatives

| Route | State on 2026-11-16 | Ownership / retention | Cost and operational result |
|---|---|---|---|
| Club Console workspace (recommended) | Ready after Team owner links the Console org | Club service account; commercial no-training terms; standard API retention; ZDR request available | $15 app/workspace cap; expected $0 incremental within included Team credit |
| Amazon Bedrock | Blocked account-wide since 2026-11-06; AWS cases `179398462200009` (restoration) and `179393410200701` (5.5 access) remain open | AWS is the processor; current Bedrock provides explicit regional retention controls ([AWS data-retention documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html)) | Regional profiles are configured at a 10% premium; no spend while blocked; retain as a fallback only after AWS confirms restoration |
| Temporary personal Anthropic key | In prod now; last usable UTC day 2026-11-19; separate $20 key limit, app budget $15 | Tied to a person; same direct-API commercial terms | Immediate outage risk: after expiry the app falls back to still-blocked Bedrock. Replace, then revoke; never extend as the durable solution |

Do not wait for Bedrock. Keep its code path and AWS cases open so restored Bedrock remains an available provider, but a blocked fallback is not continuity. Do not configure both the club and personal key; remove and revoke the personal key immediately after the club key passes a production smoke check.

## President cutover checklist

No repository or remote state was changed by this report. The following are deliberate human/production actions:

1. Sign in to `platform.claude.com` with the club-role account and create or select **Yale Undergraduate Consulting Group** as a Console organization. Add a second current officer as Admin/Billing before creating keys.
2. In `claude.ai` as the Team Primary Owner/Owner, open **Organization settings > Billing > API credits > Link organization**. Select that Console organization and accept the credit terms. Confirm the promotional credit appears in Console > Billing. Do not link a personal/test organization; Support is required to change it.
3. In Console > Workspaces, create **Production** (not Default). In Console > Spend limits, select Production and set **Monthly spend limit = $15**, exactly matching `AI_MONTHLY_BUDGET_USD`. Leave auto-reload and purchased credits off. Optionally set conservative model rate limits above the app's eight concurrent calls.
4. Create service account **club-res-website-prod**, add only that account to Production with the minimum inference-capable role, and create a key scoped only to Production with a roughly 90-day expiry. Copy it once. Do not create a personal or unscoped key.
5. Update only the JSON secret `club-res-website/prod/app`: replace `ANTHROPIC_API_KEY`; set `ANTHROPIC_API_KEY_EXPIRES` to the Console key's last intended UTC day. Do not add it to dev. Use the normal reviewed deployment; never paste it into chat, Git, a command transcript, or logs.
6. After deploy, make one synthetic, non-client Haiku request and one Sonnet draft through the app. Confirm the local Costs row increments and Console shows the Production workspace. Then disable/delete the temporary personal key.
7. Calendar a rotation at least two weeks before expiry: create a replacement service-account key, update the prod secret and deploy, test, then disable and delete the old key. Keep two officer admins current through every handoff.
8. Contact Anthropic Sales for ZDR on this Console organization. Separately decide whether to configure Console SSO after domain/IdP prerequisites are known.
9. If optional provider reconciliation is desired despite the Admin key's blast radius, create a separately expiring Admin key, add `ANTHROPIC_ADMIN_API_KEY` and `ANTHROPIC_COST_WORKSPACE_ID` to prod only, and remove it if the reconciliation is not actively used.

## Code delivered and evidence

- `backend/app/services/llm.py`: preserves expiry fail-closed behavior, exposes non-secret expiry status, accounts for Haiku 5.5's long-prompt price tier, and distinguishes a provider spend/credit stop from a transient rate limit so batch callers stop retrying.
- `backend/app/services/ai_spend.py`: local monthly summary plus optional, default-off Anthropic organization cost reconciliation.
- `backend/.env.example`: documents the club service-account key and separately privileged optional Admin key.
- `backend/tests/test_llm_provider.py`: stub coverage for expiry, provider calls and token accounting, spend-limit behavior, long-prompt pricing, and the optional cost report.

Verification observed locally:

- **Passed:** `/tmp/club-venv/bin/python tests/test_llm_provider.py` with an isolated SQLite database; it exercised both model tiers, key expiry, spend stops, long-prompt pricing, local cost aggregation, and the stubbed Admin cost response.
- **Passed:** coverage combination/report and every repository critical-module threshold (at least 80% branch coverage); `generation_policy.py` was 87% and `llm.py` 86%.
- **Passed with one unrelated suite failure:** 99 of 100 backend scripts passed across the required six parallel batches. `test_concurrent_discovery.py` failed its progress timing bound under the parallel CPU load (`gap 1.186s`, required under `20 * TICK`); it does not import or exercise these changed services and was not rerun.
- **Passed:** both report files have balanced Markdown fences and all local links resolve; the cited external primary sources were read during research.
- No server/browser smoke was applicable because no route or UI was added. No cloud, GitHub, Anthropic, Wix, DNS, or production state was read or changed.

Decision needed from the President: which officer roles will hold Owner/Admin, whether the Team-to-Console link is acceptable and unclaimed, whether to request ZDR, and whether the marginal value of provider reconciliation justifies putting a full Console Admin key on the production box. The recommendation on the last question is **no**; use the local ledger and check Console Billing manually.
