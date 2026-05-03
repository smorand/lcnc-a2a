# MCP tool configuration & discovery (US-004)

## Routes

All routes are mounted under `/agents/{agent_id}/mcp` and require an authenticated, owning user (404 leak protection via `get_agent_for_user`). The CSRF token is taken from the agent edit page's form (`/agents/{agent_id}/edit`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/agents/{a}/mcp` | List partial of attached MCP servers (HTMX) |
| GET | `/agents/{a}/mcp/new` | Empty add-form partial |
| POST | `/agents/{a}/mcp` | Create a new server row (no discovery) |
| GET | `/agents/{a}/mcp/{s}` | Edit-form partial; env/header values are MASKED |
| POST | `/agents/{a}/mcp/{s}` | Update fields; `_method=DELETE` deletes; transport change with stale `tools_cache` is rejected with HTTP 409 + body `rediscovery_required` |
| POST | `/agents/{a}/mcp/{s}/discover` | Run discovery (10s budget). On success persists `tools_cache + discovered_at` and the form-supplied transport/config |

Form fields:
- `transport` ∈ {`stdio`, `streamable_http`}
- `command`, `cwd` (stdio only)
- `url` (streamable_http only)
- `env`, `headers` are JSON-encoded objects (`{"K":"v"}`). Empty string is treated as `{}`.

## Discovery semantics

- 10s wall-clock timeout for the entire `initialize + tools/list` round trip (`mcp_client.stdio.DISCOVERY_TIMEOUT_S`).
- stdio subprocess env is **scrubbed**: only `PATH` from the parent + caller-supplied env values reach the child.
- stdio stderr is captured to a temp file and truncated to 2 KB; on failure or timeout it is appended to the 422 response body.
- `mcp_client.stdio.RECENT_SPAWNED_PIDS` records every subprocess PID the discovery loop spawned. Tests assert the PID is gone after a timeout-induced kill (the SDK's `_terminate_process_tree` runs synchronously in the context-manager exit).
- HTTP discovery uses `mcp.client.streamable_http.streamablehttp_client`; non-2xx → `mcp_discovery_failed`.

## Encryption (FR-023)

- `agent_mcp_servers.env_enc` and `headers_enc` are Fernet-encrypted JSON blobs (`json.dumps(..., sort_keys=True)`).
- The view route always re-renders with `env=""` / `headers=""`; the form shows the **keys** of the stored map followed by `********` so the user knows what is on file without leaking values.
- E2E-040 verifies that the literal plaintext value never appears anywhere in the response body or headers.

## Transport-change rule (FR-008)

Saving an existing row with a transport that differs from the persisted one is rejected with HTTP 409 + body `rediscovery_required` whenever `tools_cache IS NOT NULL`. The user must call `/discover` (passing the new transport in the form) to atomically swap config + tools_cache + discovered_at, after which a subsequent save accepts.

## Tests

- `tests/e2e/test_mcp_stdio.py` — E2E-035, 037, 038, 040, 041
- `tests/e2e/test_mcp_http.py` — E2E-036, 039
- `tests/e2e/fixtures/fake_mcp_stdio.py` — `FastMCP("fake-mcp-stdio")` with `search` and `fetch` tools
- `tests/e2e/fixtures/fake_mcp_hang.py` — sleeps forever
- `tests/e2e/fixtures/fake_mcp_fail.py` — exits 1 with `boom` on stderr
- `tests/e2e/_mcp_http_helpers.py` — respx helpers (`install_happy_path_mock`, `install_failure_mock`)
