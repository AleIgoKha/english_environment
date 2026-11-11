# Cardy MCP

The MCP endpoint is `/mcp/` (keep the trailing slash). It runs inside
the existing Django/Gunicorn app, with no additional runtime dependencies or
deployment process. It uses stateless Streamable HTTP with JSON responses;
there are no SSE streams or arbitrary SQL access. Read and add-cards credentials
are separate; the scheduled task uses only the read connection.

## Tool

`check_existing_cards` accepts an object with `phrases`: 1–100 nonblank strings,
each at most 300 characters. It returns one `{phrase, exists}` result for each
input, in order, plus `includes_learned: true`. Results include only submitted
phrases and existence flags; card backs, progress, and unrelated cards are not
returned.

Matching reuses `cards/duplicates.py`, so it agrees with CSV imports and Add/Edit:
Obverses are compared after Unicode compatibility normalization, case folding,
and collapsing whitespace. Learned cards count; Reverse and Extra do not affect
matching. Paraphrases, equivalent lessons, and deleted cards are not detected.
An unavailable deck returns a tool error, never a misleading all-new result.

The scheduled task should check candidates before generating its suggestions and omit
matches. CSV generation does not add cards or reserve phrases. Cardy's import
check remains the final protection against duplicates added in the meantime.

`add_cards` is exposed only with a separate `CARDY_MCP_WRITE_TOKEN_SHA256`
credential. It accepts 1–10 cards (`obverse`, `reverse`, `extra`), `approved: true`,
and a UUID `batch_id`. The chat skill requires the user to see and approve the
exact batch; client configuration requests approval for the write tool. The
boolean is a validation requirement, not independent proof of human consent.
The server adds atomically, skips duplicates, and saves a text-free receipt.
Retries of the same batch return its original counts; changing a batch requires
a new ID and new approval. Existing cards are never overwritten. The write
credential cannot call the read tool, and the read credential cannot add cards.

## Private access

The endpoint is **disabled by default**. Set `CARDY_MCP_TOKEN_SHA256` to the
lowercase hexadecimal SHA-256 digest of a cryptographically random bearer
token. Use at least 32 random bytes when generating the token. Store only its
digest on the server; keep the original token in private client secret settings.
Never put the token in prompts, repository files, URLs, screenshots, or logs.

Each call must send `Authorization: Bearer <private token>`. Browser login and
local test sign-in do not grant MCP access. Use a separate token from Django and
Google credentials. Changing/removing the server digest revokes the old token.
Missing or malformed configuration returns 404; invalid credentials return 401.
All responses use `Cache-Control: no-store`. Requests with a foreign Origin are
rejected, and Django's Host validation still applies. Only this token-protected
endpoint is exempt from browser CSRF checks; other routes retain CSRF protection.

Production clients must use HTTPS. The intended production URL after deployment
is `https://english-environment-production.up.railway.app/mcp/`.
This is a private Codex bearer-token integration, not an OAuth-enabled public
plugin. Deployment, actual credentials, Mac connection, and scheduled-run access
must be verified separately; implementing the endpoint does not configure them.

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

`scripts/check_production_mcp.py` verifies HTTPS health, anonymous/incorrect-token
rejection, initialization, discovery, and a read-only lookup. It discovers the
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
405 because no SSE stream or stateful session is used. Each credential exposes one tool, so
discovery needs no pagination. Requests are limited to 64 KiB.

POST requests use `Content-Type: application/json` and
`Accept: application/json, text/event-stream`. After initialization clients send
the negotiated `MCP-Protocol-Version` header. The implementation follows the
[Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
specifications. Tool annotations reflect its read-only behavior; authentication
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
