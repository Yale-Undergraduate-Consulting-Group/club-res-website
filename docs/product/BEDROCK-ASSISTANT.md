# Bedrock assistant architecture

## Current operating model

The website uses one permission-filtered assistant built on the existing application host:

1. A member registers a private, project, or club document.
2. The browser uploads the original directly to the private, versioned S3 bucket with a short-lived conditional URL.
3. After S3 metadata and versioning are verified, the backend extracts bounded text from text, Markdown, CSV, JSON, or PDF files.
4. Search chunks are stored in SQLite on retained encrypted EBS. Original bytes remain in S3.
5. Each question retrieves chunks only after applying the same owner/project/club rule used by document downloads.
6. A short prompt containing those chunks goes to Claude Haiku 4.5 through Bedrock Converse.
7. The response is stored in a member-owned thread and links back to the documents it cited.

The assistant cannot send an email, delete records, or claim that an action happened. Lookups (contacts, discovery runs, target companies) run in the same permission scope as the existing APIs. Writes (`start_find_people`, `import_run_to_contacts`) are proposed in chat and execute only after the member presses Confirm. Those operations remain explicit website actions with their existing authorization and delivery gates.

## Why this is retrieval, not training

Club documents change more often than a trained model should. Fine-tuning would create a stale copy of institutional knowledge, make deletion and access revocation difficult, and add training/evaluation cost. Retrieval keeps S3 as the durable byte store and checks access at query time. Reviewed examples can later improve prompts or evaluation sets without becoming hidden model memory.

This design also avoids an always-on vector database. Amazon Bedrock Knowledge Bases support managed ingestion and retrieval, but they require embeddings plus a vector store. The current corpus and user count do not justify that standing infrastructure. Move to S3 Vectors or another reviewed index only after retrieval latency or corpus size is measured to exceed the single-host design.

## Cost and concurrency controls

- Models are chosen by use (`TIERS` in `backend/app/services/llm.py`): Claude Haiku 5.5 for ranking and extraction, Sonnet 5.5 for drafts and Slack replies, and Opus 5.5 when a member picks it. While AWS has not enabled a 5.5 model for the account, its tier uses Haiku 4.5, Sonnet 4.6 or Opus 4.6 instead and tries the 5.5 model again every 15 minutes. The box role allows exactly these six models.
- Assistant responses are capped at 900 output tokens and 16,000 characters of retrieved context.
- Every model call is charged at list price (Bedrock offer prices plus the 10% US geo-profile premium) to one club-wide budget per calendar month, `AI_MONTHLY_BUDGET_USD` (50 by default, set in `ops/restart-yucg.sh`). A call reserves its worst case before Bedrock is called and is re-priced from the returned token counts; a call AWS refuses for the account is not charged; a call of uncertain outcome keeps its worst case. When the budget is spent, model features answer 429 until the next month. There are no per-member or per-hour model limits.
- Reservations retain the model, member, purpose, estimates, token counts and cost, never prompts or responses.
- At most two Bedrock calls execute concurrently on the current host.
- Files remain downloadable up to the workspace limit, while assistant indexing is capped at 8 MB and 600,000 extracted characters per version to protect the small server.

These values are environment configuration and must be changed through reviewed infrastructure. AWS credits are offsets and do not change the gross-cost controls.

## Security boundaries

- Retrieval joins against current document visibility and current project membership on every question.
- Administrators do not implicitly read a member's private documents.
- Conversations are visible only to their owner.
- A member can index only a document they own.
- S3 bucket names, keys, and presigned URLs are never placed in a model prompt.
- Document content is marked as untrusted. Instructions inside a document cannot authorize tools or override the assistant system instruction.
- The backend returns only citations actually present in the response.
- The Bedrock IAM policy has no training, model customization, agent, Knowledge Base, or arbitrary model-marketplace permissions.

## Expansion sequence

1. Measure source count, extraction failures, retrieval latency, token usage, and unanswered questions.
2. Add DOCX extraction and resumable background indexing if real uploads require them.
3. Read-only tools for contacts, discovery runs, and target companies use the same member scope as their existing APIs. Tool output remains untrusted context.
4. Confirmed writes are limited to starting Find people and importing a finished run into Contacts. Sending mail, deletes, and campaign release stay outside the model loop.
5. Build a regression evaluation set from administrator-approved questions, expected citations, privacy negatives, and hallucination checks.
6. Consider batch inference for non-interactive classification and summarization, since AWS discounts supported batch workloads. Consider a managed knowledge base only when measured scale warrants its additional resources.

AWS references: [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/), [Converse content blocks](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ContentBlock.html), and [Knowledge Base retrieval architecture](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-how-it-works.html).
