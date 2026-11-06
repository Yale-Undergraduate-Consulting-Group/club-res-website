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

### Club assistant: mentions, DMs, and `/yucg`

The full OAuth setup also powers the read-only club assistant. A member must use
**Connect Slack** before the assistant will return club data. The assistant can
answer free-form questions with Claude on Bedrock, but its tools enforce the
same contact, draft, campaign, project, and document access rules as the web
app. It cannot send mail or change club records.

Set `SLACK_SIGNING_SECRET` from **Basic Information → App Credentials**, then
configure:

- a `/yucg` slash command pointing to `https://<backend>/api/slack/commands`;
- an Events API request URL of `https://<backend>/api/slack/events`;
- the `app_mention` bot event and the `message.im` event.

The existing OAuth `chat:write` scope covers both `chat.postMessage` and
`chat.postEphemeral`. Channel mentions receive
an ephemeral threaded answer visible only to the asker. Direct messages receive
a normal DM. Free-form `/yucg` calls acknowledge immediately and replace their
ephemeral acknowledgement through Slack's provided response URL when ready.

The backend also needs its normal Bedrock model configuration and AI budget
settings. Busy, exhausted-budget, and unavailable-model errors are shown as
short Slack messages rather than provider details.

#### Choosing the model

Start a message with `opus:` (most capable), `sonnet:`, or `haiku:` (fastest) to
pick the model for that one question; the prefix is removed before the question
is sent, for example `@YUCG opus: compare our A24 and NEON outreach`. Without a
prefix the assistant uses the default model. A prefixed message always goes to
the model, never to the built-in shortcuts.

#### Follow-up questions (thread memory)

Replies in the same Slack thread, or in the same DM conversation, continue the
conversation. `/yucg` commands are always single-turn.

- What is stored: only the member's question and the assistant's final answer
  text, in the `slack_conversation_turns` table of the club database. Tool
  results, club documents, and Slack message text the assistant read are never
  stored.
- Who can see it: each conversation is keyed by channel, thread, and the asking
  member, so another member in the same thread never receives your earlier
  turns. In a DM without a Slack thread there is one rolling conversation per
  member.
- Retention: the last 8 turns and about 12,000 characters per conversation.
  A conversation with no new turn for 24 hours is deleted, and expired
  conversations are pruned whenever any new turn is written. Deletion is from
  the live SQLite file only; backups and snapshots of the volume may keep a copy
  until they age out.

#### What the assistant can read in Slack

Besides club data, the assistant can read Slack, read-only, to answer questions
such as "summarize this thread" or "what did we decide in #channel last week?":

- **The current thread**, or in a DM the recent DM conversation with the bot.
- **Recent history of a named channel**, but only if the asking member is a
  member of that channel and the bot can read it (it must be in the channel).
  Otherwise the assistant answers as though the channel does not exist. The
  window is at most the last 30 days and 200 messages, with long messages and
  older messages dropped to fit. Replies inside threads are not part of channel
  history; ask from the thread for those.
- Another member's DMs are never readable. User IDs are replaced with display
  names. Slack text is treated as data, never as instructions.

Slack message text read this way is sent to Bedrock to write the answer, like club
data, and is not stored by the club app (see retention above for what is).

#### Bot scopes and reinstalling

"Connect Slack" requests these scopes (`SLACK_SCOPES` in
`backend/app/routers/auth.py`):

`chat:write`, `im:write`, `im:history`, `im:read`, `app_mentions:read`,
`users:read`, `users:read.email`, `team:read`,
**`channels:history`**, **`groups:history`**, **`channels:read`**, **`groups:read`**

The last four (bold) are new for reading Slack. `im:history` was already
present. After deploying this change, add those four under **OAuth &
Permissions → Bot Token Scopes** in the Slack app, then **Reinstall to
Workspace**. Each member who connected before must press **Connect Slack** again
so the stored token carries the new scopes. Until then the Slack-reading tools
answer "not found" and everything else keeps working. Invite the bot to a channel
(`/invite @YUCG`) before asking it to read that channel.

---

## Summary

| What you see | Token type (per [Slack Tokens](https://docs.slack.dev/authentication/tokens/)) | Use in this app? |
|--------------|-------------------------------------------------------------------------------|------------------|
| Your App Configuration Tokens (Access / Refresh) | Configuration token | No – only for Manifest APIs, not for messaging |
| Token starting with `xoxb-` (e.g. from Install to Workspace) | Bot token | Yes – set as `SLACK_BOT_TOKEN` for digest |
| Client ID + Client Secret (Basic Information → App Credentials) | OAuth credentials | Yes – set as `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` for “Connect Slack” |

If your app UI never shows Client ID/Secret, create a new app **From scratch** to get them, then install that app to your workspace to get the `xoxb-` Bot token.
