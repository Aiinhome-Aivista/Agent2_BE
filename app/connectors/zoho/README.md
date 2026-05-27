# Zoho Desk / CRM Connector — SCAFFOLD

> ⚠ Auth + HTTP layer working with **region-aware URLs** (US/EU/IN/AU/CN/JP). Ticket CRUD has `TODO[ZH-N]` markers.

## What works
- OAuth 2.0 with offline access (refresh tokens)
- **Region-aware** OAuth + API hosts — Zoho's data centers are isolated; using the wrong host returns silent 401s
- `api_domain` discovery from token response (Zoho returns this, we honor it)
- Auto-refresh on 401
- `health_check()` against `/api/v1/organizations` (Desk) or `/crm/v6/users` (CRM)
- Module switch via `config.module = 'desk' | 'crm'`

## What's TODO
| Marker | Method | Hint |
|---|---|---|
| `TODO[ZH-1]` | `parse_webhook_event` | Webhook payload shape varies per event — `Ticket_Add` vs `Ticket_Update` |
| `TODO[ZH-2]` | `poll_for_changes` | `GET /api/v1/tickets?modifiedTimeRange=...` |
| `TODO[ZH-3]` | `push_create` | `POST /api/v1/tickets` — needs department + contact resolution |
| `TODO[ZH-4]` | `push_update` | `PATCH /api/v1/tickets/{id}` |

## Setup

1. Register at <https://api-console.zoho.com/> (matching your **region**!) → Add Client → **Server-based Application**
2. Set redirect URL: `http://localhost:8000/api/v1/connectors/oauth/callback`
3. Note Client ID / Secret
4. Add to `.env`:
   ```
   ZOHO_OAUTH_CLIENT_ID=...
   ZOHO_OAUTH_CLIENT_SECRET=...
   ```
5. When creating the connector in the UI, **set `region` correctly** (`us`, `eu`, `in`, `au`, `cn`, or `jp`). This determines which Zoho data center to hit.
