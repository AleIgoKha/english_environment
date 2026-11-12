# Cardy MCP

The MCP endpoint is `/mcp/` (keep the trailing slash). It runs inside
the existing Django/Gunicorn app. It uses stateless Streamable HTTP with JSON
responses; there are no SSE streams or arbitrary SQL access. OAuth uses the
already-installed Authlib library and the existing owner's Google sign-in.
Legacy separate read and write bearer credentials remain supported.

## ChatGPT and Codex plugin

Set `CARDY_OAUTH_ENABLED=true` on the deployed service and
`CARDY_PUBLIC_URL=https://english-environment-production.up.railway.app`.
The latter is the exact HTTPS issuer origin, without a path or trailing slash.
The existing Google client and callback stay unchanged. Migration 0009 creates
OAuth tables without changing cards or study progress.

The private package sources are in `integrations/cardy-plugin`. Build its archive
with `.\.venv\Scripts\python.exe scripts/build_cardy_plugin.py` from this repository;
it copies the two canonical skills and icon and writes an ignored `.local/` ZIP.
Install the private plugin through its account link, then select Connect and sign
in as the Cardy owner. Complete the connection in each host if requested. No
repository clone, Python, or pasted bearer token is required on the Mac.

OAuth provides discovery, dynamic public-client registration, authorization-code
flow with mandatory S256 PKCE, exact registered redirects, resource binding,
scope checks, and issuer identification. Only the stable ChatGPT callback, its
legacy callback-ID form, and explicit-port loopback callbacks can register.
Only an owner Google browser session can consent; local test sign-in cannot.
Consent is CSRF-protected and bound to the exact authorization query.
Codes last five minutes and are single-use. Access tokens last 30 minutes;
refresh tokens rotate with replay-family revocation and a 14-day family lifetime.
Only credential hashes are stored. `/oauth/revoke/` revokes a matching client's
token family. Disabling OAuth revokes access while disabled; changing the owner
authentication configuration invalidates old credentials as well.

Public initialization and tool discovery contain no card data. Card calls need
OAuth `cards:read` or `cards:add`. Legacy credentials retain their separate roles.
The two bundled skills require explicit approval of the exact cards and target
deck in the current chat before adding. Scheduled reviews never invoke writes.
Connection consent alone does not approve cards. Tool permission prompts are
controlled by the host; do not configure automatic approval for `add_cards`.
The server's `approved: true` field is not cryptographic evidence of a user message;
the chat skill and host permission flow enforce that interaction requirement.

The plugin grants access to Cardy, not to other conversations. Codex reviews can
use available local chat tools; ChatGPT can review only messages it can actually
access or the user supplies. Always report coverage gaps.

See [OpenAI OAuth guidance](https://developers.openai.com/plugins/build/auth) and
[plugin packaging](https://developers.openai.com/plugins/build/plugins).

## Tool

`check_existing_cards` accepts an object with `phrases`: 1–100 nonblank strings,
each at most 300 characters. It returns one `{phrase, exists}` result for each
input, in order, plus `includes_learned: true`. Matches include soft-deleted cards
as well. Results include only submitted
phrases and existence flags; card backs, progress, and unrelated cards are not
returned.

Matching reuses `cards/duplicates.py`, so it agrees with CSV imports and Add/Edit:
Obverses are compared after Unicode compatibility normalization, case folding,
and collapsing whitespace. Learned cards count; Reverse and Extra do not affect
matching. Paraphrases and equivalent lessons are not detected. Permanently
deleted cards cannot be checked or recovered.

Deletion sets `Word.deleted_at`, retaining card text and learning progress.
The default `Word.objects` manager hides deleted records from learning counts,
reset/learning routes. `Word.all_objects` is used
explicitly for duplicate checks and the owner's **All** and **Progress → Deleted**
views. All places deleted cards below every non-deleted card; the My cards count
includes deleted entries, while learning counts exclude them. Both views offer
a CSRF-protected POST Restore action in the same three-dot menu, resetting stage
to 0, clearing Learned and Deleted status, and making it due immediately. The
deleted-card menu also offers Edit (content only, retaining Deleted status),
Select, and Delete, opening a separate permanent-deletion
confirmation. Only an already-deleted card with `permanent=yes` may be physically
removed. Retrying a soft-deletion POST or submitting a stale permanent-deletion
form after restoration returns 400 instead of escalating the action.
Single-card soft deletion submits directly with CSRF protection, without a
confirmation page; GET never deletes. Bulk
selection supports Reset/Restore and Delete for every status. Its confirmation
shows soft versus permanent deletion counts and carries a snapshot of deleted
IDs; changed statuses or stale retries are rejected. Non-deleted selected cards
are soft-deleted, while already-deleted selected cards are permanently removed.
Deleting a card removes it
from the current queue and invalidates stale learning forms. MCP checks, MCP
adds, Add/Edit, and CSV imports all reserve deleted Obverses, so restoring the
original card is the way to reuse one.
Deletion from Learning records a durable study Undo action, including when the
last card is removed. Undo restores that card and its prior queue position and
progress. Deletion elsewhere, including bulk deletion, removes only the deleted
cards' Undo entries. Undo skips those cards and preserves history for other
cards; it never restores an outside deletion.
An unavailable deck returns a tool error, never a misleading all-new result.

The scheduled task should check candidates before generating its suggestions and omit
matches. CSV generation does not add cards or reserve phrases. Cardy's import
check remains the final protection against duplicates added in the meantime.

`add_cards` requires OAuth `cards:add` or the separate `CARDY_MCP_WRITE_TOKEN_SHA256`
credential. It accepts 1–100 cards (`obverse`, `reverse`, `extra`), `approved: true`,
and a UUID `batch_id`. The chat skill requires the user to see and approve the
exact batch; client configuration requests approval for the write tool. The
boolean is a validation requirement, not independent proof of human consent.
The server adds atomically, skips duplicates, and saves a text-free receipt.
Retries of the same batch return its original counts; changing a batch requires
a new ID and new approval. Existing cards are never overwritten. The write
credential cannot call the read tool, and the read credential cannot add cards.
The server rejects requests with more than 100 submitted cards before validation,
deduplication, or writes; duplicate cards still count toward this limit. The chat
prompt may request fewer cards. The 64 KiB JSON request-size limit also applies.
Do not automatically split an oversized count to bypass rejection. Duplicate
lookups accept at most 100 phrases per call.

## Legacy bearer access

### Generate tokens in Settings

The signed-in owner can open **Settings → Connect Codex** and generate or
regenerate a read or write token directly on any device. Copy the complete
`Bearer …` value into Codex's **Headers → Value**, with `Authorization` in
**Headers → Key**. The guide follows the UI's field order and requires no token
files. Keep write-tool approval set to **Ask / Prompt**.

Token generation is CSRF-protected POST only. Real owner credentials require
HTTPS; the separately configured local test account can generate credentials
for its own test database on the private network. The guide shows local names
and the local MCP URL for that account. Production rejects local test sign-in.

Migration 0010 adds a role-keyed hash-only credential table. Raw tokens are
returned once in a no-store response and held only in the current page, never
in the database, session, URL, or browser storage. Reloading loses the value;
regenerate if it was not copied. Regeneration invalidates the previous token
for that role across all app workers and devices. It does not rotate the other
role or OAuth connections. Owner authentication configuration changes invalidate
generated tokens. Generated records override the corresponding environment
credential, including after owner changes; old file tokens do not regain access.
Existing environment credentials work until replaced through Settings.

The existing setup scripts and file-based checks below are for legacy credentials;
they cannot recover or override a token generated in Settings.

With OAuth disabled and no generated tokens, the endpoint is **disabled by default**. Set `CARDY_MCP_TOKEN_SHA256` to the
lowercase hexadecimal SHA-256 digest of a cryptographically random bearer
token. Use at least 32 random bytes when generating the token. Store only its
digest on the server; keep the original token in private client secret settings.
Never put the token in prompts, repository files, URLs, screenshots, or logs.

Each legacy call must send `Authorization: Bearer <private token>`. Browser login and
local test sign-in do not grant MCP access. Use a separate token from Django and
Google credentials. Changing/removing the environment digest revokes that legacy
token when no generated record overrides it.
Missing or malformed configuration returns 404; invalid credentials return 401.
All responses use `Cache-Control: no-store`. Requests with a foreign Origin are
rejected, and Django's Host validation still applies. Only this token-protected
endpoint is exempt from browser CSRF checks; other routes retain CSRF protection.

Production clients must use HTTPS. The intended production URL after deployment
is `https://english-environment-production.up.railway.app/mcp/`.
Legacy connections are Codex-specific; use the private OAuth plugin for ChatGPT.
Deployment, account connection, and scheduled-run access must be verified
separately; implementing the endpoint does not configure them.

## Local-first setup and skills

Production uses separate `cardy` (read-only) and `cardy-add` (approval mode
`prompt`) connections. `scripts/setup_production_mcp.py` prepares distinct
production credentials in ignored `.local/` files. With `--apply-railway`, it
verifies the exact Cardy service/project and applies only the two MCP digests,
with deployment skipped so the GitHub push remains the sole release trigger.
It does not rotate Django or Google credentials or transfer any local cards.
With `--configure-codex`, it appends the two production connections to personal
Codex settings. Tokens are read by `scripts/mcp_production_headers.py`, never
stored in prompts or connection settings. Existing local settings are retained.

`scripts/check_production_mcp.py` verifies HTTPS health, OAuth discovery, anonymous
card-access/incorrect-token rejection, and an authenticated read-only lookup. It discovers the
add tool but never calls it or changes the production deck.

`scripts/setup_local_mcp.py --configure-codex` is deliberately restricted to the
separate SQLite local test deck. It creates independent private credentials in
ignored `.local/` files, saves their digests in ignored `.env`, and appends only
the `cardy-local` and `cardy-local-add` connections to personal Codex settings.
Codex reads credential headers through `scripts/mcp_local_headers.py`; no token
is included in prompts, skill files, or connection settings. Never run the header
helper manually to display its output. The add connection uses approval mode
`prompt`; read access uses `auto`.

The two skill sources are in `integrations/cardy-daily-writing` and
`integrations/cardy-add-cards`; copies are installed into personal Codex skills
for this machine. The refreshed prompt is `integrations/daily-writing-prompt.txt`.
The skills default to production and use the local test deck only when explicitly
requested. Scheduled results show a concise phrase list and overview, with a private full
batch for review. They never call add_cards. The separate add skill waits for
explicit approval of exact card contents in the chat. No schedule is created by
installing these files.

## Protocol

Supported versions: `2025-03-26`, `2025-06-18`, and `2025-11-25`. Initialization
negotiates a supported version. Supported requests are `initialize`, `ping`,
`tools/list`, and `tools/call`; notifications receive 202. GET and DELETE receive
405 for authenticated clients because no SSE stream or stateful session is used.
Unauthenticated GET returns a 401 OAuth discovery challenge. Legacy credentials
expose one tool; OAuth clients discover both tools with required scopes, so
discovery needs no pagination. Requests are limited to 64 KiB.

POST requests use `Content-Type: application/json` and
`Accept: application/json, text/event-stream`. After initialization clients send
the negotiated `MCP-Protocol-Version` header. The implementation follows the
[Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
specifications. Tool annotations distinguish reading and writing; authentication
and validation are enforced on the server, as described in
[OpenAI's MCP server guidance](https://developers.openai.com/plugins/build/mcp-server).

## Verification

Prerequisites: this project's installed `.venv` and PowerShell. These tests use
disposable credentials and databases, never the production deck:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe manage.py test cards.test_mcp --settings=config.test_settings
```

The optional integration test uses the official Python MCP SDK and a disposable
Django test HTTP server. Install the SDK in an ignored directory, leaving app
runtime dependencies unchanged. Prerequisites: the same environment, network
access for package installation, and permission to bind a temporary test port:

```powershell
Set-Location 'C:\Users\Alexei Harcenco\Documents\Repositories\english_environment'
.\.venv\Scripts\python.exe -m pip install --target .local/mcp-test-deps -r requirements-mcp-test.txt
.\.venv\Scripts\python.exe scripts/test_mcp_client.py
```

The standard suite skips the optional client test when the SDK is unavailable.
The protocol/security tests run without it. The SDK is a verification dependency,
not part of `requirements.txt` or the Docker image.
