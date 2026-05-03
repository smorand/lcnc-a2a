# US-004: MCP tool configuration & discovery

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 4
> Depends On: US-002
> Complexity: M

## Objective

Allow a logged-in user to attach MCP tool servers to an agent via two transports — `stdio` (subprocess spawned by the app) and `streamable_http` (URL with optional headers) — and run discovery to cache the server's tool list (name, description, JSON input schema). Secrets (env values, headers) are Fernet-encrypted; discovery is sandboxed and time-bounded; transport changes invalidate the cached tools and force re-discovery before save. The actual *invocation* of MCP tools at agent run-time lives in US-005.

## Technical Context

### Stack

- All from US-002 plus:
  - The official MCP Python client SDK (`mcp` package) — for `initialize` + `tools/list` over both stdio and `streamable_http` transports.
  - `asyncio.subprocess` for stdio process management.
  - `httpx` for streamable HTTP MCP discovery.

### Relevant File Structure

```
src/lcnc_a2a/
├── models/
│   └── agent_mcp_server.py        # AgentMcpServer model
├── routes/
│   └── mcp.py                     # /agents/<id>/mcp endpoints
├── services/
│   └── mcp_discovery.py           # discover_stdio(), discover_http(), normalize_tools()
├── mcp_client/
│   ├── __init__.py
│   ├── stdio.py                   # AsyncSubprocessMcpClient
│   └── streamable_http.py         # AsyncHttpMcpClient
├── templates/
│   └── agents/partials/
│       ├── mcp_list.html
│       ├── mcp_form.html
│       └── mcp_tools.html         # rendered tool list after discovery
└── tests/e2e/
    ├── test_mcp_stdio.py
    ├── test_mcp_http.py
    └── fixtures/
        ├── fake_mcp_stdio.py      # standalone python script binary used as fixture (`python -m fake_mcp_stdio`)
        └── fake_mcp_hang.py       # sleeps forever
```

### Existing Patterns

- The MCP discovery flow: spawn process / open HTTP session → send `initialize` → send `tools/list` → terminate session. The discovery client caches the resulting list as JSON `{tools: [{name, description, input_schema}]}`.
- Encryption for `env_enc` and `headers_enc` reuses `crypto.encrypt/decrypt` from US-001.
- Owner check on every endpoint goes through the agent service from US-002; mismatched owner returns HTTP 404.

### Data Model (excerpt)

#### agent_mcp_servers

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| agent_id | UUID FK → agents.id ON DELETE CASCADE | |
| transport | VARCHAR(20) | `stdio` / `streamable_http` |
| command | TEXT | nullable (stdio only) |
| env_enc | BYTEA | Fernet, nullable |
| cwd | TEXT | nullable |
| url | VARCHAR(500) | nullable (http only) |
| headers_enc | BYTEA | Fernet, nullable |
| tool_timeout_s | INTEGER | default 30, range 1..300 |
| tools_cache | JSONB | `{tools: [{name, description, input_schema}]}` |
| discovered_at | TIMESTAMPTZ | nullable until first discovery |
| created_at, updated_at | TIMESTAMPTZ | |

> NOTE: This table is created by the migration in US-003 to support the cascade test there. US-004 only fills it with real data and exposes the routes.

### Discovery Constraints

- Discovery hard timeout: 10 seconds wall-clock for the entire `initialize + tools/list` round trip.
- stdio: subprocess MUST be spawned with the parent env scrubbed; only the explicit `env` map (decrypted) is passed plus the `PATH` necessary to launch the binary.
- stdio: on timeout or non-zero exit, the spawned process MUST be terminated (`process.kill()` then awaited) and verified absent.
- HTTP: any non-2xx response or invalid MCP envelope is a discovery failure.
- Transport change on an existing row (stdio ↔ streamable_http) is rejected with HTTP 409 `rediscovery_required` until a fresh discovery is performed.

## Functional Requirements

### FR-008: Configure MCP tools (per agent)

- **Description:** Sub-form on the agent edit page. Endpoints:
  - `GET /agents/<id>/mcp` — list of currently attached servers (HTML partial).
  - `POST /agents/<id>/mcp` — add a new server (transport + connection config). Returns 200 with the form rendered including a `Discover tools` button. Discovery is NOT run yet.
  - `POST /agents/<id>/mcp/<server_id>/discover` — run discovery; returns the rendered tool list partial on success or HTTP 422 on failure.
  - `DELETE /agents/<id>/mcp/<server_id>` — remove an attached server.
- **Inputs (stdio):** `command` (full command line including args), `env` (map of name → value, values encrypted at rest), `cwd` (optional).
- **Inputs (http):** `url` (https URL), `headers` (map of name → value, values encrypted at rest), `transport` literal value `streamable_http`.
- **Outputs:** HTML partial with the discovered tool list and a save button (or an error code when discovery fails).
- **Business Rules:**
  - Discovery times out at 10 seconds; on timeout the response is HTTP 422 with body containing `mcp_discovery_timeout`.
  - Discovery failure (non-zero exit, non-2xx, invalid envelope) returns HTTP 422 with body containing `mcp_discovery_failed`. The captured stderr (stdio) or response body excerpt (HTTP) MUST be included in the error response (truncated to 2 KB).
  - Successful discovery populates `tools_cache` and sets `discovered_at = now()`.
  - On transport change for an existing row, save is rejected with HTTP 409 + body `rediscovery_required`. The user must call the `discover` endpoint again before another save will succeed.
  - GET on the MCP detail (or list) MUST mask env/header values as `********` in the rendered HTML; the literal plaintext values MUST NOT appear anywhere in the response body.

### FR-023 (applied to MCP secrets)

- `agent_mcp_servers.env_enc` and `agent_mcp_servers.headers_enc` are Fernet-encrypted. Round-trip MUST be exact.
- API responses MUST NEVER include the decrypted env values or headers.

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `fake-mcp-stdio` script | A standalone Python script (under `tests/e2e/fixtures/`) that performs the MCP `initialize` and `tools/list` handshake over stdin/stdout and exposes tools `[search, fetch]`. Invoked via `python -m tests.e2e.fixtures.fake_mcp_stdio` or as a script. | auto-generated (committed to repo as a test fixture) | ready |
| `fake-mcp-hang` script | Same interface but sleeps forever after stdin connection. | auto-generated | ready |
| `fake-mcp-fail` script | Exits with code 1 immediately on launch, writing `boom` to stderr. | auto-generated | ready |
| respx-mocked HTTP MCP | A `respx` route at `https://mcp.example.com/...` returning a valid `tools/list` envelope (`tools: [{name: "search", description: "...", inputSchema: {...}}]`). | auto-generated | ready |
| Logged-in test client | Reused from US-002 conftest. | auto-generated | ready |
| Owner-aware seed | A logged-in Alice with one agent `A`. | auto-generated | ready |

### Happy Path Tests

#### E2E-035: Add stdio MCP server with discovery happy path

- **Category:** happy
- **Scenario:** SC-007
- **Requirements:** FR-008, FR-023
- **Preconditions:**
  - The `fake-mcp-stdio` script is available; it exposes tools `[search, fetch]`.
  - Alice owns agent `A`.
- **Steps:**
  - Given Alice is logged in.
  - When Alice POSTs `/agents/<A_id>/mcp` with `{transport: "stdio", command: "python -m tests.e2e.fixtures.fake_mcp_stdio", env: {API_KEY: "k"}}`.
  - Then a new `agent_mcp_servers` row is persisted with `transport = "stdio"`, `command = "python -m tests.e2e.fixtures.fake_mcp_stdio"`, `env_enc` non-null, `tools_cache` IS NULL, `discovered_at` IS NULL.
  - When Alice POSTs `/agents/<A_id>/mcp/<server_id>/discover`.
  - Then response status is 200 and the rendered HTML partial contains the substring `search` and the substring `fetch` along with their descriptions.
  - And `tools_cache` JSON now contains an array `tools` with two entries whose `name` fields are `"search"` and `"fetch"` (set equality).
  - And each tool entry has a non-empty `description` and a non-null `input_schema` JSON object.
  - And `discovered_at` is non-null and within the last 5 seconds.
  - And `env_enc` decrypts via the Fernet key to exactly `{"API_KEY": "k"}` (Python dict equality after JSON parse of the decrypted bytes).
- **Cleanup:** Truncate `agent_mcp_servers`, `agents`.
- **Priority:** Critical

#### E2E-036: Add streamable HTTP MCP server with discovery happy path

- **Category:** happy
- **Scenario:** SC-007
- **Requirements:** FR-008, FR-023
- **Preconditions:**
  - `respx` is configured to intercept `https://mcp.example.com/...` and respond with a valid MCP `initialize` and `tools/list` envelope (one tool `search`).
  - Alice owns agent `A`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>/mcp` with `{transport: "streamable_http", url: "https://mcp.example.com", headers: {"X-Token": "t"}}` then runs the discover endpoint.
  - Then `tools_cache` contains `tools: [{name: "search", ...}]`.
  - And `headers_enc` decrypts to exactly `{"X-Token": "t"}`.
- **Cleanup:** Truncate.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-037: Discovery fails when stdio command exits non-zero

- **Category:** failure
- **Scenario:** SC-007
- **Requirements:** FR-008
- **Preconditions:**
  - Alice owns agent `A`.
  - A row was added with `command = "python -m tests.e2e.fixtures.fake_mcp_fail"` (which exits with code 1 and writes `boom` to stderr).
- **Steps:**
  - When Alice POSTs the discover endpoint.
  - Then response status is 422 and the body contains the literal `mcp_discovery_failed`.
  - And the body contains the captured stderr substring `boom`.
  - And `tools_cache` for that row is still NULL and `discovered_at` is still NULL.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-038: Discovery hangs and is killed at 10s

- **Category:** failure
- **Scenario:** SC-007
- **Requirements:** FR-008
- **Preconditions:**
  - The `fake-mcp-hang` script is available.
  - A row was added with `command = "python -m tests.e2e.fixtures.fake_mcp_hang"`.
- **Steps:**
  - When Alice POSTs the discover endpoint.
  - Then within 11 seconds the response status is 422 with the body containing the literal `mcp_discovery_timeout`.
  - And after the response returns, the spawned process is no longer running. Verification: capture the spawned PID via the discovery service (test patches `Popen` to record PIDs, or polls `os.kill(pid, 0)` until `ProcessLookupError`).
- **Cleanup:** Truncate. Ensure no orphan processes remain.
- **Priority:** High

#### E2E-039: HTTP MCP discovery rejects non-2xx response

- **Category:** failure
- **Scenario:** SC-007
- **Requirements:** FR-008
- **Preconditions:**
  - `respx` routes `https://mcp.example.com/...` to a 500 response.
  - A row was added with `transport = "streamable_http"` and that URL.
- **Steps:**
  - When Alice POSTs the discover endpoint.
  - Then response status is 422 and the body contains the literal `mcp_discovery_failed`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-040: MCP env values never returned in plain in API responses

- **Category:** side effect / security
- **Scenario:** SC-007
- **Requirements:** FR-023
- **Preconditions:**
  - Alice owns an agent `A` with an MCP server stored: `env = {"SECRET": "topsecret-MCP-value-XYZ"}`.
- **Steps:**
  - When Alice GETs `/agents/<A_id>/mcp/<server_id>`.
  - Then the response body contains the substring `********` in the position where the env value would be displayed.
  - And the response body and headers do NOT contain the substring `topsecret-MCP-value-XYZ` anywhere.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-041: Changing transport invalidates discovery (rediscovery required)

- **Category:** edge
- **Scenario:** SC-007
- **Requirements:** FR-008
- **Preconditions:**
  - Alice owns an agent with an MCP server `S` of `transport = "stdio"` with `tools_cache` populated.
- **Steps:**
  - When Alice POSTs an update to `S` switching `transport = "streamable_http"` and supplying `url = "https://mcp.example.com"` and `headers = {}`.
  - Then response status is 409 and the body contains the literal `rediscovery_required`.
  - And the persisted row still has `transport = "stdio"` (rejected save did not partially apply).
  - And after subsequently calling the discover endpoint successfully against the new transport, save is accepted (302 / 200) and the row's `transport` becomes `streamable_http`.
- **Cleanup:** Truncate.
- **Priority:** Medium

## Constraints

### Files Not to Touch

- Files from US-001 through US-003 except for the agent edit template (which gains a `<div id="mcp-list">` slot rendered by an HTMX include).

### Dependencies Not to Add

- Allowed: `mcp` (the official MCP Python SDK).
- Disallowed: any other MCP client implementation (do NOT roll your own JSON-RPC over stdin/stdout if the SDK provides it).

### Patterns to Avoid

- Do NOT pass the parent process's full environment to the spawned MCP subprocess; the spec mandates a scrubbed env.
- Do NOT log the decrypted env values or headers anywhere.
- Do NOT cache discovery results across `transport` changes.

### Scope Boundary

- Tool *invocation* (calling a tool from an agent run) is NOT in this story; that lives in US-005 with the executor.
- LLM-format conversion of the cached tool list (the `OpenAI tools format` translation, E2E-042) is NOT in this story.
- The collision-handling rule (TBD-002 in the spec) — prefixing tool names when two MCP servers expose the same name — is **NOT** required for US-004 to pass; do not implement it unless explicitly requested.

## Non Regression

### Existing Tests That Must Pass

- All US-001, US-002, US-003 tests, in particular:
  - The cascade-delete test from US-003 (deleting an agent removes its `agent_mcp_servers` rows).
  - The agent edit/save flow from US-003 must continue to work even when the agent has MCP servers attached.

### Behaviors That Must Not Change

- Agent CRUD flow remains untouched.
- Provider API key encryption format unchanged.

### API Contracts to Preserve

- All routes from US-001..US-003.
