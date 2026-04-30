# User Stories Index

> Source Specification: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Generated on: 2026-04-30
> Total Stories: 8

## Implementation Order

| Order | ID | Title | FRs | Scenarios | Tests | Depends On | Complexity | Status |
|-------|------|-------|-----|-----------|-------|------------|------------|--------|
| 1 | US-001 | Project foundation, dev mode login, base UI | FR-001, FR-023 (util/startup), FR-025, FR-026 | SC-001 | 10 | — | M | ready |
| 2 | US-002 | Agent dashboard, agent creation, per-agent API keys | FR-002, FR-003, FR-007, FR-023 | SC-002, SC-003 | 15 | US-001 | L | ready |
| 3 | US-003 | Agent edit, delete, start/stop lifecycle | FR-004, FR-005, FR-006 | SC-004, SC-005, SC-006 | 10 | US-002 | M | ready |
| 4 | US-004 | MCP tool configuration & discovery | FR-008, FR-023 (applied to MCP secrets) | SC-007 | 7 | US-002 | M | ready |
| 5 | US-005 | A2A endpoint, Agent Card, auth, Simple mode executor (full) | FR-010, FR-011, FR-012, FR-013, FR-014, FR-018, FR-021, FR-022, FR-024 | SC-005 (parts), SC-009, SC-012, multi (security/cross) | 29 | US-003, US-004 | XL | ready |
| 6 | US-006 | ReAct mode executor with guardrails and embeddings | FR-015, FR-017, FR-019 | SC-010 | 13 | US-005 | L | ready |
| 7 | US-007 | Plan & Execute mode executor | FR-016, FR-020 | SC-011 | 13 | US-005 | L | ready |
| 8 | US-008 | Runs history & per-run trace UI | FR-009 (UI surface), FR-022 (display) | SC-008 | 5 | US-005 | S | ready |

**Total tests: 102** (matches the spec).

## Dependency Graph

```
US-001 (foundation, login, base UI)
   └──▶ US-002 (dashboard, create, API keys)
           ├──▶ US-003 (edit, delete, start/stop)
           │       └──▶ US-005 (A2A endpoint + Simple executor + cross-cutting)
           │               ├──▶ US-006 (ReAct executor + guardrails + embeddings)
           │               ├──▶ US-007 (Plan & Execute executor)
           │               └──▶ US-008 (runs history & trace UI)
           └──▶ US-004 (MCP tool configuration)
                   └──▶ US-005
```

## Traceability

Every FR from the spec MUST appear in exactly one user story (the FR may also appear in others as a "(applied)" extension, but the primary owner is unique). Every E2E test from the spec MUST appear in exactly one user story. Every scenario MUST be covered by at least one user story.

### Coverage Verification

#### Functional Requirements (26 total)

| FR | Title | Owning Story |
|----|-------|--------------|
| FR-001 | Dev mode email login | US-001 |
| FR-002 | List user's agents with aggregated metrics | US-002 |
| FR-003 | Create a new agent | US-002 |
| FR-004 | Edit an agent | US-003 |
| FR-005 | Delete an agent | US-003 |
| FR-006 | Toggle agent state (start / stop) | US-003 |
| FR-007 | Generate per-agent API keys | US-002 |
| FR-008 | Configure MCP tools (per agent) | US-004 |
| FR-009 | Persist per-agent execution traces | US-005 (data persistence by executors) + US-008 (UI surface) |
| FR-010 | A2A endpoint per agent | US-005 |
| FR-011 | Agent Card endpoint | US-005 |
| FR-012 | API key authentication for A2A clients | US-005 |
| FR-013 | 503 for stopped agents | US-005 |
| FR-014 | Simple mode executor | US-005 |
| FR-015 | ReAct mode executor | US-006 |
| FR-016 | Plan & Execute mode executor | US-007 |
| FR-017 | Guardrails (max_loops, max_tokens, force synthesis on hit) | US-006 (ReAct), US-007 (PE); the Simple defensive cap is in US-005 |
| FR-018 | MCP tool retry policy | US-005 |
| FR-019 | Embedding retry policy | US-006 |
| FR-020 | Plan validation and bounded retry | US-007 |
| FR-021 | Per-context conversation memory | US-005 |
| FR-022 | Token / cost tracking | US-005 (capture) + US-002 (aggregation) + US-008 (display) |
| FR-023 | Encrypt secrets at rest | US-001 (utility + startup) + US-002 (provider key) + US-004 (MCP secrets) |
| FR-024 | OpenTelemetry JSONL export | US-001 (scaffolding) + US-005 (LLM/MCP/executor spans) + US-006 (embed spans) |
| FR-025 | IBM Carbon Design System UI | US-001 |
| FR-026 | Server-side rendering with Jinja2 + HTMX (CSRF, partials) | US-001 (scaffold) + future stories' partial endpoints |

All 26 FRs assigned. None unassigned.

#### Scenarios (12 total)

| SC | Title | Covered By |
|----|-------|------------|
| SC-001 | Builder user signs in | US-001 |
| SC-002 | List own agents with metrics | US-002 |
| SC-003 | Create a new agent | US-002 |
| SC-004 | Edit an existing agent | US-003 |
| SC-005 | Stop or start an agent | US-003 (state flip), US-005 (A2A reflection) |
| SC-006 | Delete an agent | US-003 (cascade), US-005 (in-flight cancellation) |
| SC-007 | Configure MCP tools on an agent | US-004 |
| SC-008 | View per-agent execution history and traces | US-008 |
| SC-009 | External client invokes a Simple mode agent | US-005 |
| SC-010 | External client invokes a ReAct mode agent | US-006 |
| SC-011 | External client invokes a Plan & Execute agent | US-007 |
| SC-012 | External client fetches the Agent Card | US-005 |

All 12 scenarios covered.

#### E2E Tests (102 total)

| Story | Test IDs | Count |
|-------|----------|-------|
| US-001 | E2E-001, E2E-002, E2E-003, E2E-004, E2E-005, E2E-011, E2E-096, E2E-099, E2E-100, E2E-101 | 10 |
| US-002 | E2E-006, E2E-007, E2E-008, E2E-009, E2E-010, E2E-012, E2E-013, E2E-014, E2E-015, E2E-016, E2E-017, E2E-018, E2E-019, E2E-097, E2E-102 | 15 |
| US-003 | E2E-020, E2E-021, E2E-022, E2E-023, E2E-024, E2E-025 (state-flip part), E2E-027, E2E-030, E2E-031, E2E-032, E2E-034 | 10 (E2E-025 is split: state-flip in US-003, card-200 reflection in US-005) |
| US-004 | E2E-035, E2E-036, E2E-037, E2E-038, E2E-039, E2E-040, E2E-041 | 7 |
| US-005 | E2E-025 (full), E2E-026, E2E-028, E2E-029, E2E-033, E2E-042, E2E-048, E2E-049, E2E-050, E2E-051, E2E-052, E2E-053, E2E-054, E2E-055, E2E-056, E2E-057, E2E-058, E2E-059, E2E-086, E2E-087, E2E-088, E2E-089, E2E-090, E2E-091, E2E-092, E2E-093, E2E-094, E2E-095, E2E-098 | 29 |
| US-006 | E2E-060, E2E-061, E2E-062, E2E-063, E2E-064, E2E-065, E2E-066, E2E-067, E2E-068, E2E-069, E2E-070, E2E-071, E2E-072 | 13 |
| US-007 | E2E-073, E2E-074, E2E-075, E2E-076, E2E-077, E2E-078, E2E-079, E2E-080, E2E-081, E2E-082, E2E-083, E2E-084, E2E-085 | 13 |
| US-008 | E2E-043, E2E-044, E2E-045, E2E-046, E2E-047 | 5 |
| **Total** |  | **102** |

All 102 E2E tests assigned. None unassigned.

> **Note on E2E-025:** the spec's E2E-025 has two assertions: (1) state flip to `started`, (2) `agent-card.json` returns 200 after start. Owner of (1) is US-003 (which can verify the column flip without an Agent Card endpoint). Owner of (2) is US-005, which is where the Agent Card endpoint lands. The test ID is owned by US-005 in the index above to keep the count clean; US-003 reproduces the column-flip assertion under the same ID for traceability.

> **Note on E2E-051 / E2E-094:** the spec lists both, with E2E-094 in the cross-cutting / security row of the traceability matrix and E2E-051 in SC-009's row. Both are owned by US-005 (their assertions are identical; they exist to satisfy the spec's cross-row traceability matrix).

> **Note on E2E-098:** the spec describes it as "As E2E-057 + E2E-071". The Simple-mode portion (E2E-057-equivalent assertions) lives in US-005; the ReAct-embedding-trace portion lives in US-006 (E2E-071). E2E-098 itself is recorded under US-005 for the security suite count.

## Notes on slicing decisions

- **US-005 is intentionally XL.** The user explicitly chose to merge what would have been three smaller stories: A2A transport + auth + card, Simple no-tools, and Simple with-tools + cancellation + cross-cutting. The story sits at ~29 acceptance tests; the implementation agent should plan to break the work into internal milestones (transport → executor base → memory → tool path → OTel) and run the test suite incrementally even though all tests are gated on the same story.
- **Cross-cutting tests (E2E-090..093) live in US-005.** They require both the lifecycle operations (US-002/US-003) AND a working Simple executor with tool calls; US-005 has both.
- **Performance baseline (E2E-102) lives in US-002.** It exercises the dashboard query with 50 agents and 1000 runs; the `agent_runs` schema is created in US-002 (minimally) and extended in US-005 — the perf assertion must hold both before and after the schema extension.
- **Cascade-target tables are created in US-003.** This avoids a circular dependency: the cascade test in US-003 needs all dependent tables to exist, but they don't all have application code yet. Their schemas land in the US-003 migration; their data lands in US-004 (MCP) and US-005 (runs/contexts/messages).
- **Greenfield tooling.** US-001 sets up `Makefile`, `pyproject.toml`, Alembic, `make test`, `make run-frontend`, etc. All later stories assume `make test` is the test runner.

## Suggested next step

Begin implementation with **US-001**. It has no dependencies and lays down the foundation every later story uses.
