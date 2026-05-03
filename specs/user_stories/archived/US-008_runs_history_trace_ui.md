# US-008: Runs history & per-run trace UI

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 8
> Depends On: US-005
> Complexity: S

## Objective

Expose the per-agent execution history and the per-run trace inside the builder UI: a paginated list of recent runs and an expandable trace view showing each step (thoughts, actions, observations, plans, step results, synthesis, errors) with token counts and similarity scores. Tool inputs/outputs over 4 KB render truncated with a "View full" HTMX action that streams the full payload from the database. After this story the builder is feature-complete for the MVP.

## Technical Context

### Stack

- All from US-005 (the run/step rows are already populated by the executors built in US-005..US-007).
- HTMX for the per-run expand and the "View full" action.
- Carbon Design System for table styling.

### Relevant File Structure

```
src/lcnc_a2a/
├── routes/
│   └── runs.py                       # GET /agents/<id>/runs, GET /agents/<id>/runs/<run_id>, GET /agents/<id>/runs/<run_id>/steps/<step_id>/full
├── services/
│   └── runs_view.py                  # query helpers (recent 100, expanded steps, full payload fetch)
├── templates/
│   └── agents/
│       ├── runs_list.html
│       └── partials/
│           ├── run_row.html
│           ├── run_expand.html
│           ├── step_row.html
│           └── step_full_payload.html
└── tests/e2e/
    └── test_runs_ui.py
```

### Existing Patterns

- All ownership checks reuse the agent service from US-002 (mismatched owner → 404).
- The `agent_runs` and `agent_run_steps` rows are written by the executors (US-005..US-007); this story only **reads** and renders them.
- HTMX swap target convention: `<button hx-get="/.../steps/<id>/full" hx-target="#step-<id>-content">View full</button>` with the response replacing the inner content.

### Rendering rules

- Recent runs page: lists the most recent 100 runs for the agent in `started_at DESC` order. Columns: timestamp, A2A `task_id`, A2A `context_id`, status, duration, loops, tokens in / out, USD cost (if available), summary = first 80 characters of `final_answer` (HTML-escaped, ellipsis if truncated).
- Per-run expand: renders each `agent_run_steps` row with its role-specific fields (e.g., `similarity_to_prev` for ReAct, `stage` / `step_id` / `step_status` for PE).
- Tool I/O > 4 KB: render the truncated representation (CSS class `truncated`) with a "View full" link. Storage already decides whether to truncate at 64 KB (executor-side); this story handles only the 4 KB UI threshold.
- Empty state: when an agent has zero runs, render the literal string `Send a message to /agents/<id> to see runs here.` (per the spec's exception for SC-008).

### Cost cell rule

- For a run, `cost_usd` is rendered as `$X.YYYYYY` if non-null, else the literal `n/a`.

## Functional Requirements

### FR-009 (UI surface): Per-agent execution history & traces

- **Description:**
  - `GET /agents/<id>/runs` returns the list of the 100 most recent runs.
  - `GET /agents/<id>/runs/<run_id>` returns a per-run expand partial (HTMX) with the trace steps.
  - `GET /agents/<id>/runs/<run_id>/steps/<step_id>/full` returns the full tool I/O payload (HTMX swap target) for steps where the I/O exceeds 4 KB.
- **Inputs:** Path ids, session cookie.
- **Outputs:** HTML page (list) and HTML partials (expand, full payload).
- **Business Rules:**
  - Owner check on every endpoint; cross-user / unknown id → HTTP 404.
  - Truncation rule: in the run-expand view, any step's `tool_args_json` or `tool_result_json` whose serialized JSON length exceeds 4 KB renders truncated (first 4 KB followed by `…` and a "View full" action). The full payload endpoint streams the JSON.
  - Empty state for the runs list as defined above.
  - Step rendering: order by `seq` ascending. Each row shows role, content (escaped), tool name (if any), token counts, similarity (if any), stage/step_id/step_status (PE only), duration_ms, occurred_at.

### FR-022 (display only): Cost rendering on the runs list

- The `cost_usd` cell follows the rendering rule above (display only; aggregation rules belong to FR-002 and live in US-002).

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `seed_run_with_steps(agent_id, role_seq=[...])` helper | Inserts a synthetic `agent_runs` row and a list of `agent_run_steps` rows with controlled roles, tool I/O sizes, and similarity scores. Used to drive UI tests without invoking executors. | auto-generated | ready |
| Big-payload generator | Helper that produces a JSON object whose serialization is exactly 5 KB. | auto-generated | ready |
| Two-user setup | Reused from US-002 conftest. | auto-generated | ready |

### Happy Path Tests

#### E2E-043: View runs list happy path

- **Category:** happy
- **Scenario:** SC-008
- **Requirements:** FR-009, FR-022
- **Preconditions:**
  - Alice owns agent `A`.
  - 3 `agent_runs` rows exist for `A` with varied `tokens_in`, `tokens_out`, `cost_usd`, `status`, `duration_ms`, and distinct `final_answer` strings (each at least 90 chars to exercise the 80-char summary truncation).
- **Steps:**
  - When Alice GETs `/agents/<A_id>/runs`.
  - Then response status is 200.
  - And the HTML body contains exactly 3 rows of CSS class `run-row`.
  - And for each run: tokens_in, tokens_out, cost (formatted per rule), status, duration_ms, and the first 80 characters of `final_answer` (followed by an ellipsis indicator) are present.
  - And rows are ordered by `started_at DESC` (the most recent first).
- **Cleanup:** Truncate.
- **Priority:** High

### Edge Case and Error Tests

#### E2E-044: View runs returns 404 for another user

- **Category:** failure
- **Scenario:** SC-008
- **Requirements:** FR-009
- **Preconditions:**
  - Alice owns agent `A`. Bob is logged in.
- **Steps:**
  - When Bob GETs `/agents/<A_id>/runs`.
  - Then response status is 404.
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-045: Empty runs list shows empty state

- **Category:** edge
- **Scenario:** SC-008
- **Requirements:** FR-009
- **Preconditions:**
  - Alice owns agent `A` with zero `agent_runs` rows.
- **Steps:**
  - When Alice GETs `/agents/<A_id>/runs`.
  - Then response status is 200.
  - And the body contains the literal string `Send a message to /agents/<A_id> to see runs here.` (the substring with the actual id substituted).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-046: Trace step with > 4 KB tool I/O renders truncated and full view streams full payload

- **Category:** edge
- **Scenario:** SC-008
- **Requirements:** FR-009
- **Preconditions:**
  - Alice owns agent `A` with one run `R`. `R` has one `agent_run_steps` row whose `tool_result_json` is exactly 5 KB (built via the big-payload generator).
- **Steps:**
  - When Alice GETs `/agents/<A_id>/runs/<R_id>` (expand partial).
  - Then the response HTML contains the truncated representation: an element with CSS class `truncated`, a "View full" anchor or button with `hx-get="/agents/<A_id>/runs/<R_id>/steps/<step_id>/full"`, and the rendered tool result content has length ≤ 4096 characters (excluding HTML markup; assert by parsing the relevant element's text).
  - When Alice GETs `/agents/<A_id>/runs/<R_id>/steps/<step_id>/full` (the HTMX swap).
  - Then the response status is 200 and the body contains the FULL JSON payload (5 KB, the big-payload generator output, byte-for-byte equal in the parsed JSON sense — content equality after `json.loads`).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-047: Aggregated metrics on dashboard equal SUM over runs (cross-check)

- **Category:** side effect
- **Scenario:** SC-008
- **Requirements:** FR-022
- **Preconditions:**
  - Alice owns agent `A` with several runs whose `tokens_in`, `tokens_out`, `cost_usd` fields are known (test computes the expected sums upfront: `T_in`, `T_out`, `C`).
- **Steps:**
  - When Alice GETs `/agents` (the dashboard from US-002).
  - Then the row for `A` displays exactly `tokens_in = T_in`, `tokens_out = T_out`, and the cost cell renders the formatted `$C` (with the `n/a` rule applied if any contributing run has `cost_usd IS NULL`).
- **Cleanup:** Truncate.
- **Priority:** High

## Constraints

### Files Not to Touch

- The executor implementations (US-005..US-007). This story is read-only with respect to run data.
- The dashboard aggregation query in `services/agents.py` (US-002) — only the rendering of the cost cell is exercised here, and it MUST already conform to the FR-002 rules.

### Dependencies Not to Add

- No new runtime dependencies.

### Patterns to Avoid

- Do NOT load all 100 runs' steps at once on the runs list page; load steps lazily via the per-run expand HTMX endpoint.
- Do NOT decrypt anything in the runs UI (no secrets are in run rows; this is just a defensive reminder).
- Do NOT bypass the owner check; every endpoint MUST verify `agent.user_id == current_user.id`.

### Scope Boundary

- A manual "Cancel run" button on the runs page (TBD-003 in the spec) is OUT of this story.
- Pagination beyond the most recent 100 runs is OUT of scope.
- Search/filter on the runs list is OUT of scope.

## Non Regression

### Existing Tests That Must Pass

- All US-001..US-007 tests, in particular:
  - Dashboard aggregation (US-002).
  - All executor tests (US-005, US-006, US-007).
  - Cascade-delete (US-003) — deleting an agent must still wipe `agent_runs` and `agent_run_steps`.

### Behaviors That Must Not Change

- Executor behavior unchanged.
- Builder CRUD flows unchanged.
- A2A transport unchanged.

### API Contracts to Preserve

- All routes from US-001..US-005.
