# HubSpot Connector — SCAFFOLD

> ⚠ Auth, HTTP, **and webhook signature verification** working. Tickets CRUD has `TODO[HS-N]` markers.
> HubSpot is the only scaffold with real HMAC signature verification because their docs are clear about it — bonus for you.

## What works
- OAuth 2.0 with refresh tokens
- **HMAC-SHA256 webhook signature verification** (v3 preferred, v2 fallback)
- Auto-refresh on 401
- `health_check()` against `/integrations/v1/me`

## What's TODO
| Marker | Method | Hint |
|---|---|---|
| `TODO[HS-1]` | `parse_webhook_event` | Webhook payloads are minimal — fetch full ticket via `/crm/v3/objects/tickets/{id}` |
| `TODO[HS-2]` | `poll_for_changes` | `POST /crm/v3/objects/tickets/search` with `hs_lastmodifieddate` filter |
| `TODO[HS-3]` | `push_create` | `POST /crm/v3/objects/tickets` |
| `TODO[HS-4]` | `push_update` | `PATCH /crm/v3/objects/tickets/{id}` |

## Setup

1. Create app at <https://developers.hubspot.com/> → Apps → Create app
2. Add scopes: `tickets`, `crm.objects.contacts.read`
3. Set redirect URL: `http://localhost:8000/api/v1/connectors/oauth/callback`
4. Add to `.env`:
   ```
   HUBSPOT_OAUTH_CLIENT_ID=...
   HUBSPOT_OAUTH_CLIENT_SECRET=...
   ```
5. After connecting, register your webhook URL in **Settings → Webhooks** in the HubSpot app dashboard.
