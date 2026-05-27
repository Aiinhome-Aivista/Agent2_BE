# Salesforce Connector — SCAFFOLD

> ⚠ Auth + HTTP layer working. CRUD, polling, and webhook parsing have `TODO[SF-N]` markers.

## What works
- OAuth 2.0 Web-Server Flow with **PKCE**
- Sandbox vs production login (via `config.sandbox: true`)
- Refresh tokens
- `instance_url` discovery during token exchange
- Auto-refresh on 401, retry/backoff
- `health_check()` against `/services/data`

## What's TODO
| Marker | Method | Hint |
|---|---|---|
| `TODO[SF-1]` | `parse_webhook_event` | Use a custom Apex trigger that POSTs JSON via `HttpCallout` |
| `TODO[SF-2]` | `poll_for_changes` | SOQL: `SELECT Id, CaseNumber, Subject, Description, Status, Priority FROM Case WHERE LastModifiedDate >= :cursor` |
| `TODO[SF-3]` | `push_create` | `POST /services/data/v59.0/sobjects/Case` |
| `TODO[SF-4]` | `push_update` | `PATCH /services/data/v59.0/sobjects/Case/{Id}` |

## Setup

1. **Create a Connected App**: Setup → App Manager → New Connected App
   - Enable **OAuth Settings**
   - Callback URL: `http://localhost:8000/api/v1/connectors/oauth/callback`
   - Selected OAuth Scopes: `api`, `refresh_token`, `offline_access`
   - Require Secret for Web Server Flow: ✅
2. Wait ~10 minutes after saving — Salesforce takes time to propagate.
3. Add to `.env`:
   ```
   SALESFORCE_OAUTH_CLIENT_ID=your_consumer_key
   SALESFORCE_OAUTH_CLIENT_SECRET=your_consumer_secret
   ```
