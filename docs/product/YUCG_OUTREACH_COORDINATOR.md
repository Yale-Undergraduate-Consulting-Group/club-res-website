# YUCG Outreach Coordinator

End-user guide for the **Outreach Coordinator** page (`/yucgoutreach`) in the Client Affairs Tools app.

## What it does

- Loads prospects from `data/YUCG_Prospect_List.xlsx` (synced on the server).
- Filters and ranks companies for outreach week.
- Recommends targets with **rules** or **Ollama** (YUCG website context).
- Shows **verifiable** rationale (spreadsheet row, verification URL, YUCG site excerpt).
- Exports a CSV shortlist for the team.

A second tab, **Company discovery**, runs the existing multi-source contact finder for one company at a time.

## Prerequisites

1. Backend running (`./start-all.sh` or `uvicorn` on port 8000).
2. Spreadsheet present at `data/YUCG_Prospect_List.xlsx` in the repo.
3. For Ollama recommendations: Ollama running locally; optional custom model per `scripts/ollama_create_yucg.sh`.
4. Optional: run `python scripts/sync_prospects_from_xlsx.py` if your deployment uses SQLite sync (Agent 1).

## Workflow

### 1. Sync spreadsheet (server)

Ensure the latest Excel is in `data/YUCG_Prospect_List.xlsx`. After updating the file, restart the API or run the sync script so list endpoints see new rows.

### 2. Open Coordinator tab

Navigate to **Outreach Coordinator** in the app nav (route `/yucgoutreach`). The default tab is **Coordinator**.

### 3. Filter the Prospect Board

- **Sector** — narrow to aviation, studios, etc.
- **Min incentive** — numeric threshold (0–100); default 50 on first load.
- **Contact type chips** — incentivized types are highlighted (amber): Airport ASD, Fleet Planning, Studio Strategy, Head of Client Affairs, Partnerships.
- **Search** — filter by company name (`/` focuses search when not in an input).

Click **Refresh board** after changing filters.

### 4. Rules-based recommend

1. Set filters to match your outreach week theme.
2. Leave **Rules-based** selected under **Recommend Targets**.
3. Click **Run recommendation**.
4. Review the results table; click **Verify details** on any row.

Rules weight incentive score, outreach priority, Yale hook, and contact-type match.

### 5. Ollama recommend

1. Switch toggle to **Ollama**.
2. Click **Run recommendation**.
3. If Ollama is offline, the UI shows a clear error (no silent failure).
4. Open **Verify details** to see website corpus citation and reasoning chain.

### 6. Verify before outreach

The verifiability drawer shows:

- Score breakdown (incentive, priority, Yale hook, contact type match)
- Verification source URL from the spreadsheet
- YUCG service tags from engagement theme
- Website citation URL + excerpt (Ollama mode)
- Reasoning steps and suggested message angle

Only email contacts you can verify independently (LinkedIn, press, SEC patterns in the sheet).

### 7. Export shortlist

- Select rows on the Prospect Board with checkboxes, **or**
- Use row indices from the latest recommendation run.

Click **Export shortlist** to download `YUCG_outreach_shortlist.csv` for the outreach week.

## API routes (for integrators)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/yucg/prospects/meta` | Spreadsheet source, sectors, contact types |
| GET | `/api/yucg/prospects` | List/filter prospects |
| GET | `/api/yucg/prospects/recommend` | Rules-based top N |
| POST | `/api/yucg/prospects/ai-recommend` | Ollama top N |
| POST | `/api/yucg/prospects/export-shortlist` | CSV export `{ row_indices: number[] }` |

Discovery runs remain under `/api/yucgoutreach/*`.

### API smoke script

With backend running and a JWT in `YUCG_TOKEN` (copy `yucg_token` from browser localStorage after login):

```bash
chmod +x scripts/smoke_yucg_coordinator.sh
export YUCG_TOKEN="…"
./scripts/smoke_yucg_coordinator.sh
```

Exercises meta, list, rules recommend, CSV export, and optional Ollama recommend.

## Manual test checklist (smoke)

Run with backend + spreadsheet in place:

- [ ] `/yucgoutreach` loads; nav label **Outreach Coordinator** / mobile **Targets**.
- [ ] **Coordinator** tab: Prospect Board loads (or shows empty/sync message without 500).
- [ ] Sector filter and min incentive change the row count.
- [ ] Incentivized contact-type chips filter (amber styling visible).
- [ ] **Rules-based** recommend returns rows; **Verify details** drawer opens with breakdown.
- [ ] **Ollama** recommend: success with Ollama up, or readable error with Ollama stopped.
- [ ] **Export shortlist** downloads a CSV when rows are selected or recommended.
- [ ] Prospect Board **select-all** checkbox selects visible rows for export.
- [ ] Incentivized contact types show **amber badges** in the table column.
- [ ] **Company discovery** tab: start run, see progress, export Excel still works.
- [ ] `npm run build` in `frontend/` passes.

## UI reference (screenshots)

When documenting screenshots, capture:

1. **Coordinator overview** — header, tab menu, Prospect Board with amber contact-type chips.
2. **Recommend panel** — Rules vs Ollama toggle and results table.
3. **Verifiability drawer** — open on the right with score breakdown and YUCG citation block.
4. **Discovery tab** — unchanged run form + prospects table.

## Related files

- `frontend/src/pages/YucgOutreach.tsx` — page UI
- `frontend/src/api.ts` — `api.yucg` client (`prospectsMeta`, `listProspects`, `recommend`, `aiRecommend`, `exportShortlist`)
- `frontend/src/lib/navConfig.ts` — nav label
- `backend/app/services/prospect_coordinator.py` — spreadsheet loader (Agent 3)
- `backend/app/services/yucg_ollama_recommender.py` — Ollama (Agent 4)
