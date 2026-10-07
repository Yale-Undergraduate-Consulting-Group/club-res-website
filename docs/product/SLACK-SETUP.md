# Slack setup (per official Slack token docs)

This app can use Slack for (1) **"Connect Slack"** in Profile (per-user OAuth) and (2) **daily digest** posts. Which tokens you need depends on what you see in the Slack app config.

Reference: [Slack – Tokens](https://docs.slack.dev/authentication/tokens/)

---

## If you only see "Your App Configuration Tokens" (Access Token / Refresh Token)

In Slack’s documentation, tokens shown under **Your App Configuration Tokens** are [**Configuration tokens**](https://docs.slack.dev/authentication/tokens/#config). They are **only** for [App Manifest APIs](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests#manifest_apis) to create and configure apps. They **cannot** be used for sending messages (`chat.postMessage`) or for this app’s digest.

So the two tokens you have from that section are the wrong type for posting to Slack. You need a **Bot token** instead.

---

## What this app needs to send messages

Sending messages (and the daily digest) requires a [**Bot token**](https://docs.slack.dev/authentication/tokens/#bot). Bot tokens **start with `xoxb-`**. You get one by **installing the app** to a workspace via [OAuth](https://api.slack.com/authentication/oauth-v2).

To install via OAuth you need:

1. **Client ID** and **Client Secret**  
   Slack’s docs say these are in the app’s [**Basic Information**](https://api.slack.com/apps) tab, under **App Credentials**.  
   If your app’s Basic Information page does **not** show an “App Credentials” section (and you only see Configuration Tokens elsewhere), create a **new** app: [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**. That flow always shows Client ID and Client Secret in Basic Information.

2. **Install the app to a workspace**  
   In your app: **OAuth & Permissions** → add a **Redirect URL** (e.g. `http://localhost:8000/api/auth/slack/callback`) → **Install to Workspace**. After you install, Slack shows a **Bot User OAuth Token** that starts with `xoxb-`. That is the token you use for posting.

---

## Two ways to use Slack in this app

### Option A: Full OAuth (“Connect Slack” + per-user DMs)

In `backend/.env` set:

- `SLACK_CLIENT_ID` = from Basic Information → App Credentials  
- `SLACK_CLIENT_SECRET` = from Basic Information → App Credentials  
- `BACKEND_URL` = e.g. `http://localhost:8000`

Then users can click “Connect Slack” in the app and get digest DMs. You must have completed the OAuth setup (redirect URL, scopes) and have Client ID/Secret from Basic Information.

### Option B: Digest only (no “Connect Slack”) – use your Bot token

If you already have a token that **starts with `xoxb-`** (from Install to Workspace), you can use it only for the **daily digest**:

In `backend/.env` set:

- `SLACK_BOT_TOKEN` = your `xoxb-...` token  
- `SLACK_DIGEST_CHANNEL_ID` = the channel ID (e.g. `C01234ABCD`). In Slack: right‑click the channel → View channel details → copy the ID at the bottom.

“Connect Slack” in the app will not work with only these; the scheduled daily digest will post to the given channel.

---

## Summary

| What you see | Token type (per [Slack Tokens](https://docs.slack.dev/authentication/tokens/)) | Use in this app? |
|--------------|-------------------------------------------------------------------------------|------------------|
| Your App Configuration Tokens (Access / Refresh) | Configuration token | No – only for Manifest APIs, not for messaging |
| Token starting with `xoxb-` (e.g. from Install to Workspace) | Bot token | Yes – set as `SLACK_BOT_TOKEN` for digest |
| Client ID + Client Secret (Basic Information → App Credentials) | OAuth credentials | Yes – set as `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` for “Connect Slack” |

If your app UI never shows Client ID/Secret, create a new app **From scratch** to get them, then install that app to your workspace to get the `xoxb-` Bot token.
