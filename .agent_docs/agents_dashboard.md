# Agents dashboard, creation, and API keys (US-002)

## Routes

- `GET /agents` (`routes/dashboard.py`): renders `agents/list.html` for the
  current user. Anonymous → 302 `/login`. Iterates over
  `services.agents.list_agents_with_metrics(user_id, window_days)` and emits
  one `<tr class="agent-row">` per row.
- `GET /agents/new` (`routes/agents.py`): renders the create form.
- `POST /agents` (`routes/agents.py`): validates via
  `schemas.agent_form.validate_create_agent_form`; on `AgentFormError` returns
  HTTP 400 with the form re-rendered and the contract code (e.g.,
  `name_required`, `name_taken`, `prompts_required`, `max_steps_out_of_range`)
  in the body. On success, persists the agent (provider key Fernet-encrypted),
  generates the first `default` API key, sets the plaintext key on a 5-minute
  httponly cookie keyed `agent_key_once::<id>`, and 302-redirects to
  `/agents/<id>`.
- `GET /agents/{id}` (`routes/agents.py`): 404 with body `not_found` if the
  agent does not belong to the user (no existence leak across users). Reads
  the one-time cookie if present and clears it on the response. The provider
  API key is rendered as `********`; the actual plain provider key is never in
  the response.
- `POST /agents/{id}/keys` (`routes/agents.py`): mints another API key and
  returns the `agents/partials/api_key_once.html` HTML partial.

## Aggregation rules

`services.agents.list_agents_with_metrics` runs one indexed query joining all
runs in the configured window (`Settings.metrics_window_days`, default 30):

- `requests = COUNT(*)`
- `tokens_in / tokens_out = COALESCE(SUM, 0)`
- `avg_duration_ms / avg_loops = AVG` (NULL when no rows)
- `total_time_ms = COALESCE(SUM(duration_ms), 0)`
- `last_run_at = MAX(started_at)`
- `total_cost_usd`: SUM, but if any row in the window has `cost_usd IS NULL`
  the cost cell renders the literal `n/a` instead.

Failed runs still contribute to counters; only the time window filters rows.

## API key generation

`services.api_keys.generate_api_key` uses `secrets.token_urlsafe(32)` (~43
chars base64url). Storage:

- `key_hash = sha256(plain).digest()` (32 raw bytes, unique index)
- `key_last4 = plain[-4:]` (CHAR(4) for fingerprint display)
- `revoked_at = NULL` initially

The plain key is shown once on the agent detail page right after creation
(`agents/partials/api_key_once.html`) inside an element with
`data-copy-target="<plain>"`. The plain key is never persisted, logged, or
returned again.

## Tables

- `agents`: full schema per spec §8 with `UNIQUE (user_id, name)`,
  `INDEX (user_id)`, `status` defaulting to `stopped`.
- `agent_api_keys`: unique index on `key_hash`, plain index on `agent_id`.
- `agent_runs`: minimal columns needed for aggregation; full schema lands in
  US-005. Index `(agent_id, started_at DESC)`.
