# Jira Cloud Connector — Setup Guide

This is the **reference connector** in the framework — fully implemented,
end-to-end working against real Atlassian Jira Cloud accounts.

---

## What works

| Capability | Status |
|---|---|
| OAuth 2.0 (3LO) authorization with refresh tokens | ✅ |
| Per-call rate-limit handling + auto token refresh on 401 | ✅ |
| Inbound webhooks (signed via per-connector URL token) | ✅ |
| Polling fallback (every 5 minutes by default) | ✅ |
| Bidirectional issue ↔ incident sync | ✅ |
| Add comments to issues | ✅ |
| Status transitions (workflow lookup → fire) | ✅ |
| Field mapping with custom value translation | ✅ |
| Webhook auto-refresh (Atlassian expires them every 30 days) | ✅ |
| Multi-site picker (when token has access to >1 Atlassian site) | ⚠ first site picked automatically — UI picker is the easy next step |
| Attachments | ❌ not yet (issue events trigger refresh, no file copy) |

---

## Step 1 — Create an OAuth (3LO) app on Atlassian

1. Sign in at <https://developer.atlassian.com/console/myapps/>
2. Click **Create → OAuth 2.0 (3LO)**
3. Name it something like `Intelligent Incident Agent (Local Dev)`
4. **Permissions** → **Add APIs**:
   - Jira API
   - Add scopes:
     - `read:jira-work`
     - `write:jira-work`
     - `manage:jira-webhook`
     - `read:me`
5. **Authorization** → set the callback URL:
   ```
   http://localhost:8000/api/v1/connectors/oauth/callback
   ```
   (For production, put your real domain. Atlassian allows multiple URLs.)
6. **Settings** → copy the **Client ID** and **Secret**

---

## Step 2 — Configure the backend

Add to `backend/.env`:

```bash
# Jira OAuth app credentials (from step 1)
JIRA_OAUTH_CLIENT_ID=your-client-id-here
JIRA_OAUTH_CLIENT_SECRET=your-client-secret-here

# Master key for credential encryption — generate ONCE and never lose it.
# (Losing it means re-authenticating every connector.)
CREDENTIAL_MASTER_KEY=paste-output-of-Fernet.generate_key()-here

# Public URL where Atlassian's webhooks should reach you.
# For local dev, use ngrok / cloudflare-tunnel:
#     ngrok http 8000
WEBHOOK_PUBLIC_BASE_URL=https://your-ngrok-subdomain.ngrok.app
```

Generate a master key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Step 3 — Connect from the UI

1. Start backend + frontend (`uvicorn app.main:app --reload`, `npm run dev`)
2. Sign in as admin → **Settings → Integrations → Jira → Connect**
3. You'll be redirected to Atlassian, asked to consent, and bounced back
4. The connector page will show:
   - Status: **Connected**
   - Site: your `*.atlassian.net` URL
   - Logged-in user (from `/myself`)
5. Set the **Project Key** (e.g. `OPS`) in the connector config — this is
   where outbound-created issues will land.

---

## Step 4 — Verify webhooks

After connecting:

1. Click **Register Webhook** in the connector detail page
2. The backend calls `POST /rest/api/3/webhook` with your public URL +
   per-connector secret token
3. Edit any issue in Jira → within seconds it should appear in the
   **Incident Queue** and the **Audit Log** should show:
   ```
   Jira webhook received → upsert from issue ABC-123
   ```
4. If nothing happens, check:
   - `WEBHOOK_PUBLIC_BASE_URL` is reachable from the public internet
     (ngrok URL must be HTTPS and forwarding to port 8000)
   - The webhook record exists: `GET /rest/api/3/webhook` via Postman
     using your token
   - Jira's webhook delivery logs: <https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/webhook>

---

## Step 5 — Set up polling (already running)

Polling is the safety net for missed webhooks. It runs every 5 minutes
by default (configurable via `connectors.poll_interval_sec`). To start
the worker:

```bash
celery -A app.workers.celery_app worker --loglevel=info
celery -A app.workers.celery_app beat --loglevel=info
```

The beat scheduler enqueues `connectors.poll_jira` every interval.

---

## Field mappings

Default mappings live in `app/connectors/jira/mappings.py`:

| Local | Jira |
|---|---|
| `subject` | `fields.summary` |
| `description` | `fields.description` (ADF↔plain-text) |
| `priority` | `fields.priority.name` (P1↔Highest, P2↔High, …) |
| `status` | `fields.status.name` (lookup table) |
| `category` | `fields.issuetype.name` |
| `tags` | `fields.labels` |
| `caller` | `fields.reporter.displayName` |

Override via the **Field Mappings** UI; the rules persist in
`connector_field_mappings`.

---

## Troubleshooting

**"401 Unauthorized" after working initially**
→ The access token expired and the refresh token may have rotated.
   The HTTP client auto-refreshes once. If it still fails, the refresh
   token itself is invalid and the connector status flips to `expired`.
   User must reconnect.

**Webhooks stop firing after ~30 days**
→ Atlassian dynamic webhooks expire. The `refresh_jira_webhooks` Celery
   task runs daily and calls `PUT /rest/api/3/webhook/refresh` to extend
   them. Check celery beat is running.

**"No accessible Atlassian sites"**
→ The OAuth consent didn't grant your app access to a Jira site.
   Re-run the connect flow and ensure the user clicks "Allow" with at
   least one site selected.

**Description shows up as raw `{"type":"doc",...}`**
→ The ADF→plain-text converter (`adf_to_plain` in `api_client.py`) only
   handles paragraph + text nodes. For bulleted lists, code blocks, etc.,
   extend the function — Atlassian's full ADF spec is at
   <https://developer.atlassian.com/cloud/jira/platform/apis/document/>.
