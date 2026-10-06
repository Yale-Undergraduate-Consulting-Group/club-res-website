# One image = Vite SPA + FastAPI. Local `start-all.sh` is unchanged (venv + Vite).
FROM node:22-alpine AS fe
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
# npm can silently skip a platform-specific optional dependency (npm/cli#4828).
# The CSS minifier is a native module, so a skipped install does not fail here
# — it fails minutes later inside `npm run build` with
# "Cannot find module ../lightningcss.linux-x64-musl.node", and it fails only
# sometimes: the identical tree built clean four minutes earlier. Install, then
# prove both native bindings load before going on.
RUN for attempt in 1 2 3; do \
      npm ci --no-audit --no-fund && node -e "require('lightningcss'); const nested='./node_modules/@tailwindcss/node/node_modules/lightningcss'; if (require('fs').existsSync(nested)) require(nested);" && break; \
      echo "npm skipped a native optional dependency; retrying (attempt $attempt)"; \
      rm -rf node_modules; \
    done; \
    node -e "require('lightningcss'); const nested='./node_modules/@tailwindcss/node/node_modules/lightningcss'; if (require('fs').existsSync(nested)) require(nested);"
COPY frontend ./
RUN npm run build

FROM python:3.12.14-alpine3.24
WORKDIR /app
RUN apk upgrade --no-cache
COPY backend/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend .
COPY --from=fe /fe/dist /app/frontend_dist
ENV FRONTEND_DIST=/app/frontend_dist
RUN addgroup -g 10001 yucg && adduser -D -u 10001 -G yucg yucg \
    && mkdir -p /data && chown -R 10001:10001 /app /data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
