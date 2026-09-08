# MVP Gap Audit

**Audited:** 2026-09-08 · **Baseline:** 1143 tests passing, 62 API operations, no frontend
**Method:** every `[M]` requirement traced to running code and, where it exists, to a test that
executes it. Nothing below is marked COMPLETE because a helper function exists — the criterion is a
reachable API contract with a test behind it.

---

## A. The headline finding

**The MVP acceptance criterion cannot currently be met, and the reason is not the backend.**

PRD §1.17 defines completion as: *"a user can, on a clean install with seeded data, execute the
17-step demo flow in 12-DEMO-FLOW.md without a developer present."* All 17 steps of that flow are
**screen** actions — "log in", "land on `/dashboard`", "click Assess", "drag the amount slider",
"open the Behaviour tab". Doc 03 specifies 27 numbered screens.

There is no frontend. Steps 1–17 are therefore unexecutable as documented, regardless of how complete
the API becomes. Every backend gap below is fixable in this session; the frontend is not.

---

## B. Requirement matrix

Legend: **COMPLETE** reachable + tested · **PARTIAL** partly reachable · **MISSING** no code ·
**BLOCKED** needs something the documentation does not define.

### FR-1 Authentication and authorisation

| ID | Requirement | Status | Evidence / gap |
|----|-------------|--------|----------------|
| FR-1.1 | Login, JWT access + rotating refresh | COMPLETE | `app/services/auth.py`; `test_api_auth.py` |
| FR-1.2 | Argon2id, ≥12 chars, complexity, **last 5 rejected** | PARTIAL | Hashing/complexity done. Reuse history **BLOCKED** — Doc 05 defines no `password_history` table |
| FR-1.3 | One role per user | COMPLETE | `users.role_id`; enforced by schema |
| FR-1.4 | Every endpoint declares its permission | COMPLETE | `require_permission`; `test_permissions.py` guards the whole route table |
| FR-1.5 | Lockout 5 attempts / 15 min | COMPLETE | `test_api_auth.py::test_five_failures_lock_the_account` |
| FR-1.6 | Reset by single-use 30-min emailed token | PARTIAL → **implementing** | `/auth/forgot-password` exists and is non-enumerable; `/auth/reset-password` missing. Needs a token table (Doc 05 gap) |
| FR-1.7 | Forced change on first login | COMPLETE | `must_change_password` cleared on change; tested |

### FR-2 Route management

| ID | Status | Evidence |
|----|--------|----------|
| FR-2.1 CRUD + soft delete | COMPLETE | `DELETE /routes/{id}` added this phase |
| FR-2.2–2.9 | COMPLETE | Route engine + `test_api_routes.py` (58 tests) |

### FR-3 Applicant management

| ID | Requirement | Status | Gap |
|----|-------------|--------|-----|
| FR-3.1–3.4 | Register, store, obligations auto-sum | COMPLETE | `test_api_customers.py` |
| FR-3.5 | **Bureau fetch through pluggable provider, persist raw** | MISSING → **implementing** | Mock adapter exists, no endpoint reaches it |
| FR-3.6 | **Document upload, MIME/size validation, outside web root** | MISSING → **implementing** | — |
| FR-3.7 | Duplicate prevention on national ID | COMPLETE | 409 on duplicate hash |
| FR-3.8 | Versioned financial profile | COMPLETE | Snapshotted onto the application |

### FR-4 Credit scoring and underwriting

| ID | Status | Evidence |
|----|--------|----------|
| FR-4.1–4.10 | COMPLETE | `test_api_workflow.py` (80 tests) — assess, decide, override, policy caps |
| FR-4.11 four-eyes above threshold | PARTIAL | Maker≠checker enforced. Amount threshold is **[S] Phase 2** and no value is documented |

### FR-5 Loan management

| ID | Status | Evidence |
|----|--------|----------|
| FR-5.1–5.5 | COMPLETE | Booking, schedule, allocation, DPD, BR-4 classification; 54 unit + 80 integration tests |

### FR-6 Portfolio monitoring

| ID | Requirement | Status | Gap |
|----|-------------|--------|-----|
| FR-6.1 | **Daily telematics ingestion** | MISSING → **implementing** | Mock adapter exists, no ingestion job or endpoint |
| FR-6.2 | Battery metrics ingestion | MISSING → **implementing** | — |
| FR-6.3 | 7/30-day baselines and % change | PARTIAL | Rule engine consumes them; nothing computes them |
| FR-6.4 | Inferred monthly revenue | PARTIAL | Same |
| FR-6.5 | **Per-loan monitoring view** | MISSING → **implementing** | `GET /loans/{id}/monitoring` |
| FR-6.6 | Stale-feed detection | PARTIAL | Rules exist; the metric is never populated |

### FR-7 Early warning

| ID | Requirement | Status | Gap |
|----|-------------|--------|-----|
| FR-7.1–7.6 | Rules as data, AND/OR, nightly + on-demand, dedup, auto-resolve | COMPLETE | 22 rules; `test_api_alerts.py` |
| FR-7.7 | **Full lifecycle incl. IN_PROGRESS, ESCALATED** | PARTIAL → **implementing** | Only acknowledge/resolve exist |
| FR-7.8 | **Assignment with SLA by severity** | MISSING → **implementing** | SLA due-times are stored; no assignment endpoint |

### FR-8 Dashboards

| ID | Requirement | Status | Gap |
|----|-------------|--------|-----|
| FR-8.1 | **Role dashboard with the §1.11 KPI cards** | MISSING → **implementing** | `/portfolio/summary` covers part; `/dashboard/summary` absent |
| FR-8.2 | 7 of 9 charts | MISSING | Chart data endpoints absent |
| FR-8.3 | Risk table, server-side paging/sort/filter | COMPLETE | `GET /loans`, `GET /portfolio` |

### FR-9 Administration and audit

| ID | Requirement | Status | Gap |
|----|-------------|--------|-----|
| FR-9.1 | Scoring config editor, live weight validation | MISSING → **implementing** | Read-only today |
| FR-9.2 | **Publish creates immutable version; previous queryable** | MISSING → **implementing** | Versions exist in data; no publish workflow |
| FR-9.3 | Loan rules editor | MISSING → **implementing** | Settings exist as rows; no editor endpoint |
| FR-9.4 | Risk rules editor | MISSING → **implementing** | Read-only today |
| FR-9.5 | User and role management | MISSING → **implementing** | — |
| FR-9.6 | Append-only audit of every write | PARTIAL → **implementing** | Written for auth, decisions, loans; **no reader endpoint** |
| FR-9.7 | System settings | MISSING → **implementing** | — |

### Non-functional / security

| Requirement | Source | Status |
|---|---|---|
| Rate limiting (5/min per IP on login) | Doc 09 §9.6 | MISSING → **implementing** |
| Security headers, CORS policy | Doc 09 §9.5 | COMPLETE |
| PII masking, no secrets in logs | Doc 09 §9.7, TRD §2.11 | COMPLETE — redaction shared by audit + logs |
| Structured JSON logs, `/metrics` | TRD §2.11 | COMPLETE |
| Background jobs with `job_runs` | TRD §2.10 | PARTIAL — 3 of 10 implemented |
| Scheduler | TRD §2.10 | MISSING — MVP mechanism is "container cron → CLI", so a **CLI entrypoint** is the deliverable, not a scheduler process |

### Frontend

| Requirement | Status |
|---|---|
| 27 documented screens (Doc 03) | **MISSING — no frontend directory** |
| 17-step demo flow (Doc 12) | **BLOCKED on the above** |

---

## C. Implementation plan (this session)

Ordered by dependency; each item lands with tests.

| # | Deliverable | Files | Tests |
|---|---|---|---|
| 1 | Bureau fetch endpoint | `services/bureau.py`, `routers/customers.py` | integration: fetch, cache-within-validity, audit |
| 2 | Vehicle catalogue | `repositories/catalog.py`, `services/catalog.py`, `routers/vehicles.py` | list/get/create, filters, authz |
| 3 | Stateless `/scoring/*` | `routers/scoring.py`, `services/scoring.py` | parity with persisted assess; no rows written |
| 4 | Documents | `services/document.py`, `routers/documents.py` | MIME allow-list, size cap, path traversal, authz, audit |
| 5 | `/loans/{id}/monitoring` | `services/monitoring.py` | series + derived trends |
| 6 | `/dashboard/summary` | `services/dashboard.py` | KPI cards per §1.11 |
| 7 | Alert lifecycle | `services/alert.py`, `routers/alerts.py` | assign, activity, in-progress, escalate, SLA, authz, audit |
| 8 | Admin surface | `routers/admin.py`, `services/admin.py` | config draft/publish, rules CRUD, users, settings, audit reader |
| 9 | Telemetry ingestion + baselines | `services/telemetry.py`, jobs | ingest, baseline, stale feed |
| 10 | Reset password | migration + `services/auth.py` | single-use, expiry, invalidation, audit |
| 11 | Rate limiting | `core/ratelimit.py` | 429 on burst, per-IP |
| 12 | CSV export | `routers/reports.py` | authz, filters, `DATA_EXPORTED` audit |

**Not attempted this session:** the 27-screen frontend. It is the single largest remaining item and
the blocker on the documented acceptance criterion.

---

## D. Conflicts found between documentation and schema

Recorded rather than silently resolved, per the standing instruction.

| # | Conflict | Resolution taken |
|---|---|---|
| 1 | FR-1.2 requires the last 5 passwords rejected; Doc 05 defines no `password_history` table | Added `password_history` + `password_reset_tokens` in migration `0002`, following Doc 05's conventions. Both are required by `[M]` requirements that cannot otherwise be met |
| 2 | FR-1.6 requires an emailed reset token; no table and no SMTP in MVP infra (D-4 lists SMTP as a Phase 5 dependency) | Token issued, stored hashed, returned only in non-production so the flow is testable; delivery is the documented external dependency |
| 3 | Doc 06 §6.14 lists `POST /loans`; §6.8 says a loan is created by booking an approved application | Booking is authoritative (`POST /applications/{id}/book`). `POST /loans` is not implemented; recorded in the contract matrix |
| 4 | Doc 06 lists `GET /dashboard/summary`; the implementation shipped `GET /portfolio/summary` | Both served — `/dashboard/summary` is canonical per the document |
