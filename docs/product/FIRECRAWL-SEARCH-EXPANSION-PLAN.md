# Firecrawl Integration + Assistant Search Expansion — Handoff Plan

Status: **plan only, nothing in this document has been applied**. Written for a coding
agent to execute end to end. Every claim below was verified against the running
systems on 2026-09-17 (OCI VM logs/DB, AWS read-only audit, and direct reads of
`backend/app/services/assistant_service.py`) — this is not speculative.

## 0. Systems in play

| System | Where | Role |
|---|---|---|
| Firecrawl (self-hosted) | OCI Always-Free VM `oracle-slotbank` (150.136.53.176, Tailscale `100.84.7.57:3002`) | Headless-browser web fetch (Camoufox stealth backend) |
| InternIntel | same OCI VM (`internintel-worker`/`internintel-cycle.timer`) + local Mac | Personal recruiting-intel pipeline, consumes Firecrawl |
| YUCG Outreach ("client affairs tools") | AWS, `t3.small` EC2 (`i-09a071e22270b027c`) behind CloudFront, stack `YucgOutreach-dev` | Yale Undergraduate Consulting Group's member outreach app — **this is the target for the 12-concurrent/70-total-user AI search expansion** |

No existing code path connects these two AWS/OCI systems. Any Firecrawl usage inside
YUCG is 100% new integration work — confirmed by a full-repo grep (`firecrawl|oracle|OCI|100.84.7.57|slotbank`
→ zero matches in the YUCG repo).

---

## 1. Fix list — broken things, in dependency order

Each item names the exact file/config and the exact defect. Fix top-to-bottom; later
items depend on earlier ones being done first.

### 1.1 OCI VM: classification stage is dead (blocks InternIntel, not YUCG, but must be fixed before OCI's Firecrawl can be treated as "the shared collector" for anything else)
- **File**: `/srv/internintel/repo/.env` on the OCI VM.
- **Defect**: `SLOTBANK_HOST=http://127.0.0.1:8080` — points at itself; nothing listens there. Every crawl job that reaches the classification step dies with `"No local model: start slotbank on :8080 (preferred) or Ollama."` — confirmed: 61/88 crawl_job rows are `DEAD` with exactly this error, 0 are anything else.
- **Fix**: set `SLOTBANK_HOST` to the Mac's Tailscale IP (`http://100.x.x.x:8095` — the router built this session, not the plain `:8080` serve) once condition 1.2 is met.

### 1.2 This Mac is logged out of Tailscale
- **Check**: `tailscale status` → `Logged out`.
- **Defect**: even with 1.1 fixed, the OCI VM has no path back to this Mac right now.
- **Fix**: `tailscale up` on this Mac (interactive login — the agent doing this must have the user complete the browser auth step, cannot do it unattended).

### 1.3 AWS EC2 (YUCG box) is not on the tailnet
- **Defect**: the YUCG backend has no network path to the OCI VM's Firecrawl API (`100.84.7.57:3002`) at all today — it isn't joined to the same tailnet.
- **Fix**: install/join Tailscale on the EC2 instance (`i-09a071e22270b027c`) via SSM (do **not** touch the CDK stack — see 1.4). This is the only network change required; no security groups/NAT/ALB changes needed since Tailscale is a userspace mesh overlay.

### 1.4 Guardrail — do not touch YUCG's CDK stack
- **File**: `infra/README.md` in the YUCG repo, explicit warning already there.
- **Constraint, not a bug**: `cdk deploy YucgOutreach-dev` mints a new EC2 instance (`userDataCausesReplacement=true`), which breaks the CloudFront VPC-origin binding and detaches the SQLite EBS volume. All app changes ship via GitHub Actions → ECR → SSM container restart. The agent executing this plan must use that path, never `cdk deploy`.

### 1.5 No Firecrawl adapter exists in the YUCG backend
- **Files that currently do web fetching, all with hand-rolled logic and no shared abstraction**:
  - `backend/app/services/contact_scraper.py`
  - `backend/app/services/web_contact_discovery.py` (`_tavily_search`, line 40)
  - `backend/app/services/yucgoutreach_discovery.py` (`_tavily_search`, line 53)
  - `backend/app/services/research_providers.py` (`search_sources`, Tavily + direct `httpx`/BeautifulSoup fetch capped at 300KB, line 89)
  - `backend/app/routers/contacts.py` (`search_person`, line 564 — Tavily + optional Bedrock summary)
- **Defect**: each of these calls Tavily/BeautifulSoup directly and independently; there is no provider interface to slot Firecrawl behind. Adding Firecrawl today means editing five call sites, not one.
- **Fix**: introduce one adapter (`backend/app/services/web_fetch.py`, new file) with a single function `fetch_page(url) -> {content, links, screenshot?}` that calls the OCI Firecrawl API (`POST http://100.84.7.57:3002/v0/scrape` or `/v1/scrape` depending on the deployed Firecrawl API version — check the running container's `/v0` vs `/v1` route before wiring) and route all five call sites through it. Keep Tavily as-is for *search* (Firecrawl doesn't do search, it does fetch/crawl of known URLs) — Firecrawl replaces the BeautifulSoup/httpx *fetch* step, not the Tavily *discovery* step.

### 1.6 No admission control for a new shared consumer of the OCI box
- **Defect**: the OCI VM is 2 OCPU/12GB and currently serves InternIntel alone. Adding YUCG as a second consumer without a shared limit risks one tenant starving the other, and the box has no proxy configured (`PROXY_SERVERS` empty in `docker-compose.override.yml`) — heavier crawl volume raises IP-block risk for both tenants simultaneously.
- **Fix**: reuse the existing `DISCOVERY_JOBS` semaphore pattern already in `contact_scraper.py`/`web_contact_discovery.py` (1–4 concurrent) for calls that go through the new `web_fetch.py` adapter, AND add an hourly cap on Firecrawl calls from YUCG (mirror the Bedrock pattern already in place: 15/hr/member, 120/hr club-wide — reuse those exact numbers/mechanism, don't invent a new one).

### 1.7 Assistant search is a single-shot keyword scanner, not "maximal querying"
- **File**: `backend/app/services/assistant_service.py`.
- **Defects, all confirmed by direct code read**:
  - `retrieve_context()` (line 187): ranks by `lowered.count(term)` — plain substring counting, no stemming/synonyms/BM25/embeddings. Pulls up to 2500 chunk candidates (`LIMIT 2500`, line 204) but only ever keeps the **top 12** (line 214) and stops once **16,000 chars** (`MAX_CONTEXT_CHARS`, line 31) are filled.
  - `answer()` (line 498): exactly **one** LLM call per question (`max_tokens=900`, line 518) — no multi-hop retrieval, no "search again if the first pass wasn't enough."
  - Conversation history: last **6 messages**, **1,500 chars** each, **6,000 chars** total (line 507) — long threads lose context fast.
  - Documents >8MB are never indexed (`MAX_INDEX_BYTES`, line 28/80) — hard ceiling regardless of relevance.
- **This is section 2 below** — the actual expansion work.

---

## 2. Search feature expansion spec ("maximal querying")

Goal: the assistant should be able to actually search the full corpus relevantly, not
a keyword-count sample of the first 12 hits. Four independent upgrades, ordered by
cost/risk (do 2.1 first — it's a pure win with no new infra):

### 2.1 Replace naive substring counting with SQLite FTS5 + BM25 (no new infra)
- SQLite ships FTS5 built-in; no new service, no embeddings model, no vector DB needed.
- Add a new virtual table:
  ```sql
  CREATE VIRTUAL TABLE assistant_chunks_fts USING fts5(
    content, title, content='assistant_document_chunks', content_rowid='id'
  );
  ```
  with triggers to keep it in sync on insert/delete in `assistant_document_chunks`
  (same file that creates that table — `workspace_schema.py`).
- Replace the Python-side `lowered.count(term)` loop in `retrieve_context()` with
  `SELECT ... FROM assistant_chunks_fts WHERE assistant_chunks_fts MATCH ? ORDER BY bm25(assistant_chunks_fts) LIMIT ?`.
  This alone fixes ranking quality (BM25 accounts for term frequency AND document
  length AND rarity — substring-count does none of that) and lets you safely raise
  the candidate pool without a Python-side score-everything pass eating CPU on the
  `t3.small` box.

### 2.2 Make the context/chunk budget adaptive, not a fixed top-12/16k cut
- Current: always top 12 chunks, hard stop at 16,000 chars, regardless of how many
  chunks actually scored well or how much headroom Haiku's context window has.
- Change `retrieve_context()` to take a token budget derived from the model's actual
  context window minus the prompt scaffolding + history + expected output — Claude
  Haiku 4.5's context is far larger than 16k tokens; this cap was clearly sized
  defensively, not to the model's real limit. Raise `MAX_CONTEXT_CHARS` and the
  top-N chunk count together, gated by a real token count (use a tokenizer, not a
  char-count proxy) so you don't silently truncate mid-sentence.

### 2.3 Multi-hop retrieval instead of one-shot
- `answer()` currently does exactly one `complete_text()` call. For "maximal
  querying," let the assistant issue a second retrieval pass when its first answer
  is low-confidence or explicitly says it needs more — e.g., have `operator_system_prompt()`
  support a `"search_again": "<refined query>"` field in its structured payload;
  if present, call `retrieve_context()` again with the refined query terms (capped
  at 2 hops total, to bound Bedrock cost under the existing 15/hr/member ceiling).
- Raise `max_tokens=900` only if paired with this — more context in means answers
  legitimately need more room out; 900 is tight for anything beyond a short summary.

### 2.4 Feed Firecrawl-sourced web content into the same retrieval path
- Once 1.5 (Firecrawl adapter) exists, index fetched web pages into
  `assistant_document_chunks` the same way uploaded documents are (reuse `_chunks()`
  and `index_document_version()`'s chunking, just with a web-fetch content source
  instead of an S3 object). This means "maximal querying" also covers live web
  content the assistant fetched during discovery, not just uploaded files —
  answering the original ask ("ensure maximal querying prompted via those AIs").

---

## 3. Wiring plan — sequenced, with acceptance criteria per step

Ready to hand to a coding agent as-is. Each step names its exact target file(s) and
a concrete, checkable acceptance criterion — no step should be marked done on vibes.

1. **Unblock InternIntel's classifier** (OCI `.env` + Tailscale login).
   *Acceptance*: `sqlite3 /srv/internintel/live.sqlite "select status,count(*) from crawl_job group by status"` shows new jobs completing as `DONE`, not `DEAD` with the slotbank error, within one `internintel-cycle.timer` tick (5 min).
2. **Join the YUCG EC2 box to the tailnet** (via SSM, never CDK).
   *Acceptance*: `curl http://100.84.7.57:3002/v0/health` (or whatever health route the deployed Firecrawl version exposes) succeeds *from inside the EC2 instance* over SSM Session Manager.
3. **Add `backend/app/services/web_fetch.py`** (the Firecrawl adapter) and route the five call sites in 1.5 through it, gated by the `DISCOVERY_JOBS` semaphore and a new hourly cap mirroring Bedrock's.
   *Acceptance*: a manual `search_person`/`start_find_people` call in staging (`develop` branch, no-deploy environment per the repo's 3-branch model) returns content fetched via Firecrawl, visible in Firecrawl's own container logs on the OCI VM as a new `/scrape` hit.
4. **FTS5 migration** (2.1) — new virtual table + sync triggers in `workspace_schema.py`, swap `retrieve_context()`'s ranking.
   *Acceptance*: existing `backend/tests` for `assistant_service` (check current coverage first — IMPLEMENTATION-STATUS.md reports 34% overall backend coverage, so this module may need new tests written, not just existing ones kept green) pass, plus a new test asserting BM25 ordering beats substring-count ordering on a constructed corpus where naive counting would rank a long-but-low-relevance chunk above a short-but-on-topic one.
5. **Adaptive context budget** (2.2) — token-count-based, not char-count-based.
   *Acceptance*: a question with a large matching corpus retrieves more than 12 chunks when the model's real context window allows it, verified by logging retrieved-chunk-count and total-token-count per call for a week and confirming it varies with corpus size instead of always hitting exactly 12/16000.
6. **Multi-hop retrieval** (2.3).
   *Acceptance*: a deliberately two-part question ("what does Company X do, and who are its consultants' current champions there") triggers a second retrieval pass, observable via the `search_again` field appearing in `parse_operator_payload()` output for that request, and both hops staying inside the existing per-member Bedrock hourly cap.
7. **Web-fetched content indexed into the same retrieval path** (2.4).
   *Acceptance*: a page fetched via Firecrawl during a `start_find_people` run becomes retrievable by a subsequent assistant question, with a citation ID pointing at the fetched page rather than only ever citing uploaded documents.

Ship every step via GitHub Actions → ECR → SSM restart, per the repo's existing
deploy model — never `cdk deploy` (step 1.4). Land each numbered step as its own PR
against `develop` (no-deploy) → promote through the existing 3-branch flow
(`develop` → feature/`Beta` → `main`/Production), matching how this repo already ships.
