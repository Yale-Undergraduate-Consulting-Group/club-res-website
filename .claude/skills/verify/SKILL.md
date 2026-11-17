---
description: Run this repository's checks for the files that changed, run the change itself, and report what ran and what did not. Use before committing or opening a PR.
when_to_use: Use on your own after changing code, before committing, and before saying a task is done.
---

AGENTS.md ("Verification and completion") is the policy; this skill is how to run it.

## Changed files

- Committed on this branch: !`git diff --name-only "$(git rev-parse -q --verify origin/integration || echo origin/main)"...HEAD`
- Not yet committed: !`git status --short`

## Checks by changed path

Run only the checks that match the changed files, from the directory in the table. Other contributors may share the working tree: do not run `npm ci`, reset, or clean it unless `frontend/package-lock.json` changed or `node_modules` is missing.

| Changed path | Run from | Check |
|---|---|---|
| `scripts/`, `tests/`, `.github/` | repo root | `python3 -m unittest discover -s tests -p 'test_*.py'` |
| `.github/workflows/`, `.github/actions/` | repo root | `actionlint` |
| `terraform/` | repo root | `terraform -chdir=terraform fmt -check -recursive`, then `init -backend=false -lockfile=readonly -input=false`, `validate`, `test` |
| `frontend/` | `frontend/` | `npm run lint`, `npm run test:unit`, `npm run build`, `bash ../scripts/npm_audit_gate.sh` |
| `backend/` | `backend/` | `JWT_SECRET=ci-test-secret-not-a-known-default-xx .venv/bin/python scripts/test_suite.py` |
| `docker/`, `backend/requirements*`, `frontend/package*.json` | repo root | Container check below |
| Only `docs/` or top-level `*.md` | | Check links and commands in the changed text |

- Backend needs Python 3.12 with `requirements-dev.txt` (`coverage`, not pytest). Set up once: `cd backend && python3.12 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock && .venv/bin/pip install -r requirements-dev.txt` (two installs: the dev file has no hashes). The suite runs every `tests/test_*.py` as a script with branch coverage and an 80% floor on critical modules. One file: `.venv/bin/python tests/test_x.py` from `backend/`.
- `npm_audit_gate.sh` must run from `frontend/`. From the repo root `npm audit` finds no lockfile, and the script still exits 0 with `::warning::... dependencies were NOT scanned`. That is **not run**, not passed.
- Dependencies: regenerate `backend/requirements.lock` with the command in its header.
- If a tool is not installed (`terraform` is often missing), say so. Do not report the check as passed.
- Never run `terraform apply`, `plan`, or any AWS command here. These checks need no cloud credentials.

## Run the change

Passing tests do not prove the feature works. Run it and look. Use free ports and temporary files, and remove them afterwards.

**Frontend or UI.** Run the affected Playwright specs against a dev server on a spare port, with a temporary config that you delete afterwards. The specs mock `/api` themselves, so they need no backend. Screenshots go to `/tmp` so they do not overwrite another run's `test-results/`. Then open the PNG for the changed screen, and check its error and empty states:

```sh
cd frontend
PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
cat > playwright.verify.temp.config.ts <<EOF
import base from './playwright.config';
const url = 'http://127.0.0.1:$PORT';
export default {
  ...base, reporter: 'list', retries: 0,
  use: { ...base.use, baseURL: url, screenshot: 'on' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port $PORT --strictPort', url, reuseExistingServer: false },
};
EOF
npx playwright test -c playwright.verify.temp.config.ts --output=/tmp/verify-pw tests/hub.spec.ts --project=desktop
rm playwright.verify.temp.config.ts   # screenshots: /tmp/verify-pw/*/test-finished-1.png
```

Replace `tests/hub.spec.ts` with the specs for the changed screen, and run `--project=mobile` too when layout changed. To see the page against a real API, start the backend below, then `VITE_PROXY_TARGET=http://127.0.0.1:<api port> npm run dev -- --host 127.0.0.1 --port <spare port> --strictPort`.

**Backend.** Start the server on a spare port with a throwaway database (`APP_ENV=beta` sends no mail), seed an admin, and call the changed endpoint with and without a token. Replace the last two `curl` lines with the changed route:

```sh
cd backend
export JWT_SECRET=local-verify-secret-0123456789abcdef APP_ENV=beta DATABASE_URL=sqlite:////tmp/verify.db
rm -f /tmp/verify.db*
PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
.venv/bin/python -m uvicorn main:app --port "$PORT" > /tmp/verify-api.log 2>&1 &
API=$!
curl -sf --retry 40 --retry-all-errors --retry-delay 1 "http://127.0.0.1:$PORT/api/health"
sqlite3 /tmp/verify.db "INSERT INTO users (id, email, name, role, is_active) VALUES (1, 'admin@yale.edu', 'Admin', 'admin', 1)"
TOKEN=$(.venv/bin/python -c "from app.jwt_utils import create_token; print(create_token(1, 'admin@yale.edu', role='admin'))" | tail -1)
curl -s -o /dev/null -w "anonymous: %{http_code}\n" "http://127.0.0.1:$PORT/api/ai/models"
curl -s -o /dev/null -w "admin: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/ai/models"
kill $API; rm -f /tmp/verify.db* /tmp/verify-api.log
```

Expected for the example route: `anonymous: 401`, `admin: 200`. `backend/.env` is loaded over your shell variables, so it must not set `DATABASE_URL`.

**Container** (`docker/`, requirements, anything the image runs). The check from AGENTS.md, then the CI smoke test:

```sh
docker buildx build --platform linux/amd64 -f docker/app.Dockerfile --tag club-app:verify --load .
trivy image --severity HIGH,CRITICAL --exit-code 1 club-app:verify
docker run --rm --network none --entrypoint python -e JWT_SECRET=local-verify-secret-0123456789abcdef -e DATABASE_URL=sqlite:////data/smoke.db club-app:verify scripts/container_smoke.py
docker run -d --rm --name club-verify -p 127.0.0.1:8011:8000 -e JWT_SECRET=local-verify-secret-0123456789abcdef -e APP_ENV=beta -e DATABASE_URL=sqlite:////data/smoke.db club-app:verify
curl -sf --retry 30 --retry-all-errors --retry-delay 2 http://127.0.0.1:8011/api/health
docker stop club-verify; docker image rm club-app:verify
```

Expected: `{"status":"ok","service":"YUCG Outreach"}`. Use another host port if 8011 is taken.

## Rules

- If the diff matches a trigger in [REVIEW.md](../../../REVIEW.md), run that rule's check too.
- If a check fails, show the failing output and fix the code. Never edit the test to make it pass, add sleeps, or rerun until it goes green. A flaky test is a bug to explain, not to retry.
- **Three strikes:** after three failed attempts to fix the same failing check, stop and report to a human with the command, the output, and what you tried.
- Terraform is optional for local work. The Intake stage does not require it; the Dev stage checks it.

## Report

List each check with **passed**, **failed**, or **not run** and the reason, and what you ran and looked at to exercise the change. Name the files that no check covered.
