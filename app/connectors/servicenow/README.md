# ServiceNow Connector — SCAFFOLD

> ⚠ **This is a scaffold.** Authentication and the HTTP layer work end-to-end
> against a real instance, but the business logic (sync, push, parse webhook)
> is intentionally stubbed with `TODO[SN-N]` markers. Use the Jira connector
> as a reference implementation.

## What works today

- OAuth 2.0 authorization-code flow (real `oauth_token.do` endpoint)
- Token refresh on 401 (single-flight, retried once)
- Encrypted credential storage
- `health_check()` against `/api/now/table/sys_user`

## What you need to implement

Each TODO marker tells you the exact API call and the recommended approach:

| Marker | What | Why it's a TODO |
|---|---|---|
| `TODO[SN-1]` | `parse_webhook_event` | Webhook payload shape depends on the Business Rule you write in your instance — there's no universal default |
| `TODO[SN-2]` | `poll_for_changes` | One-line implementation, but query parameters depend on whether you use `sys_updated_on` or `sys_created_on` |
| `TODO[SN-3]` | `push_create` | Field selection depends on whether you write to `incident`, `change_request`, or a custom table |
| `TODO[SN-4]` | `push_update` | Same as above |
| `TODO[SN-5]` | `push_comment` | Trivial — one POST — included for completeness |

## Setup checklist

1. **Instance OAuth setup**: Navigate to **System OAuth → Application Registry → New** in your ServiceNow instance. Pick "Create an OAuth API endpoint for external clients." Note the Client ID and Secret.

2. **Add to `.env`**:
   ```bash
   SERVICENOW_OAUTH_CLIENT_ID=...
   SERVICENOW_OAUTH_CLIENT_SECRET=...
   ```

3. **Create the connector** in the UI; supply `instance_url` (e.g. `https://acme.service-now.com`).

4. **Implement the TODOs above.**

5. **Set up the Business Rule** in ServiceNow that pushes incident events to:
   ```
   {WEBHOOK_PUBLIC_BASE_URL}/api/v1/connectors/{connector_id}/webhook?token={webhook_secret}
   ```
