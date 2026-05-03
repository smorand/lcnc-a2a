# Runs history & per-run trace UI (US-008)

Read-only UI built on the rows already populated by the executors (US-005..US-007).

## Routes (`routes/runs.py`)

| Method | Path | Returns |
|--------|------|---------|
| GET | `/agents/<id>/runs` | full HTML page; 100 most recent runs ordered by `started_at DESC` |
| GET | `/agents/<id>/runs/<run_id>` | HTMX expand partial; `agent_run_steps` rows ordered by `seq` |
| GET | `/agents/<id>/runs/<run_id>/steps/<step_id>/full` | streamed full JSON payload (compact `json.dumps(..., sort_keys=True)`) |

Every endpoint hits `services.agents.get_agent_for_user`; cross-user / unknown ids → HTTP 404 with body `not_found`.

## Truncation rule (FR-009)

`services/runs_view.py::truncate_payload` returns the first ``PAYLOAD_TRUNCATE_THRESHOLD - 1 = 4095`` characters of the serialized JSON followed by an ellipsis (`…`), so the rendered text inside ``<pre class="step-payload-text">`` is at most 4096 characters. The wrapping `<div class="truncated">` carries the "View full" `hx-get`. The full endpoint returns the raw JSON (round-trip equal under `json.loads`).

## Empty state

When an agent has zero runs the template renders the literal string ``Send a message to /agents/<agent_id> to see runs here.`` (matches the spec exception for SC-008).

## Cost rendering

The runs-list cell renders `cost_usd` as `$X.YYYYYY` (six decimals) when non-null, otherwise the literal `n/a`. The dashboard aggregation in `services.agents.list_agents_with_metrics` is unchanged; the dashboard's existing "any null → n/a" rule still wins for the per-agent total.

## Templates

- `agents/runs_list.html` - full page
- `agents/partials/run_row.html` - one row + an inline expand target (`#run-<id>-trace`)
- `agents/partials/run_expand.html` - swap target body for the expand HTMX call
- `agents/partials/step_row.html` - one step row, including the truncated/full payload UX

## Tests

`tests/e2e/test_runs_ui.py` (E2E-043..047). Helpers `_seed_run_full` and `_seed_step` insert directly via SQL so the UI tests do not depend on any executor code path.
