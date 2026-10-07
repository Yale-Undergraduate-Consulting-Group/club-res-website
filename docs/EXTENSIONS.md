# Planned extensions

Nothing here is implemented or deployed. Each entry names the trigger that justifies building it, the design constraints from [CI_CD.md](CI_CD.md), and what must be true before work starts. Claims marked *unverified* were not checked against live AWS or current pricing.

| Extension | Trigger | Main prerequisite |
|---|---|---|
| Managed knowledge base for the assistant | Measured retrieval failure, below | Embedding quota above zero; reviewed design |
| Custom domain | A club domain exists | ACM certificate in us-east-1, DNS access (CI_CD.md section 7) |
| Egress filtering proxy | Crawl and MX lookups move to a separate worker | A worker that needs no arbitrary internet access (section 14) |
| Base images pinned by digest | Any reproducibility or supply-chain review | A reviewed process for bumping two digests |
| Restore drill and deletion evidence | Before holding real client data in prod | An exercised EBS-snapshot and `.backup` restore |
| Uptime alarm and synthetic check | Prod is live | A notification target |
| Organization owned by the club | The host organization's owner changes or leaves | `organization/` re-applied from the new management account (section 15) |

## Managed knowledge base for the assistant

**Current design.** Chunks live in SQLite on the encrypted data volume and are filtered by the same owner, project and club rule as downloads before each question reaches Claude Haiku 4.5 through Bedrock Converse (`docs/product/BEDROCK-ASSISTANT.md`). That document already defers a managed knowledge base until measured scale justifies it, and the Terraform instance policy deliberately grants no agent or knowledge-base permissions.

**Build only when one of these is measured** (thresholds are proposals for the maintainers to confirm):

- An administrator-approved evaluation set shows relevant documents missed by SQLite search for more than 20% of questions.
- Members need documents beyond the 8 MB and 600,000-character indexing cap.
- Retrieval latency on the box exceeds 3 seconds at the 95th percentile.

**Prerequisites (checked 2026-10-06, read-only, us-east-2).**

- Titan Text Embeddings V2 and Cohere Embed v4 are offered. The IAM simulator allowed `bedrock:CreateKnowledgeBase`, `bedrock:Retrieve`, `s3vectors:CreateVectorBucket` and `aoss:CreateCollection` for the builder account.
- Every Bedrock quota in the account, embedding models included, was 0. Ingestion cannot run until the quota is raised and the account's Bedrock access is verified.

**Design.**

1. One knowledge base on **S3 Vectors** with Titan Text Embeddings V2, reading the documents bucket and encrypted with the environment key. S3 Vectors is pay per use; OpenSearch Serverless carries a standing minimum and Aurora pgvector conflicts with the repository rule against an always-on Aurora database (*unverified pricing*).
2. The backend calls `Retrieve`, never `RetrieveAndGenerate` and never a Bedrock Agent. The model call stays on the existing guarded Converse path so the per-member and per-club limits, transactional cost reservations and response caps still apply.
3. Each document is ingested with metadata `document_id`, `version_id`, `owner_id`, `project_id` and `visibility`. The backend builds the metadata filter from the session; the browser never supplies it.
4. **Defense in depth:** after `Retrieve`, the backend re-joins every passage to current document visibility and project membership in SQLite and drops any passage that is no longer visible. Metadata lags behind revocation, so the filter alone is not sufficient.
5. Passages remain untrusted prompt content, exactly as today.

**Why not Bedrock Agents.** An agent orchestrates tool calls outside the application, so it would bypass the permission join, the rate limits and the cost reservations. Tool use stays in the application loop.

**Infrastructure changes (Terraform).** The knowledge base, its service role (read the documents bucket, use the KMS key, write the vector bucket, invoke the embedding model), a scheduled ingestion job, and on the instance role `bedrock:Retrieve` on this one knowledge base only. Update `.trivyignore` and the CI_CD.md data-store table in the same change.

**Deletion and revocation.** Deleting an S3 object does not delete its vectors. Deleting a document must call the knowledge-base document delete and verify the vector is gone before the application reports success. Document this next to the existing deletion semantics in CI_CD.md section 10.

**Tests required before release.**

- A member cannot retrieve another member's private document, a project document after removal from the project, or a document after revocation, including during the sync lag window.
- A deleted document never appears in an answer or citation.
- An injected instruction inside a retrieved passage cannot trigger a tool or override the system instruction.
- Ingestion failure leaves the SQLite path serving answers.

**Rollout.** Dev first, with documents copied under the dev data rules. A setting selects `sqlite` or `knowledge_base` retrieval so the change can be reverted without a deploy. Measure answer quality, latency and cost against the SQLite baseline before prod.

**Open questions.** Which metadata fields Retrieve filters can combine without losing recall (S3 Vectors added metadata pre-filtering on 2026-09-30; confirm it applies through Bedrock Knowledge Bases); the ingestion cost per megabyte; whether Cohere Embed v4 retrieves better than Titan V2 on club documents.
