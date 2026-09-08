# 14. Development Backlog

**Priorities:** **P0** critical (MVP blocker) · **P1** high (MVP, degrade gracefully if late) ·
**P2** medium (Phase 2) · **P3** low (future)
**Complexity:** S ≤ 1 day · M 2–3 days · L 4–6 days · XL > 1 week (must be split before starting)
**Sprint length:** 2 weeks. Sprint numbers map to the phases in [11-MVP-ROADMAP.md](11-MVP-ROADMAP.md).

Summary: **96 stories · 61 P0 · 19 P1 · 12 P2 · 4 P3.**

---

## EPIC A — Platform Foundation *(Sprint 1)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| A-01 | Project scaffold | As a developer, I want a one-command local environment, so that onboarding takes minutes. | P0 | M | — | `docker compose up` yields a working stack on a clean machine in < 5 min; README documents it; `.env.example` lists every variable. |
| A-02 | Database schema | As a developer, I want the complete schema as a migration, so that all environments are identical. | P0 | L | A-01 | All 28 tables + enums + triggers + partitions created by `alembic upgrade head`; `downgrade base` then `upgrade head` round-trips cleanly. |
| A-03 | Reference seed | As an operator, I want roles, permissions, settings, scoring configs and risk rules seeded, so the system is usable on first boot. | P0 | L | A-02 | 6 roles, 38 permissions, 3 ACTIVE scoring configs (+1 archived), 22 risk rules, 10 vehicle models, 25 charging stations; idempotent (re-running changes nothing). |
| A-04 | API skeleton | As a developer, I want middleware, error handling and health endpoints in place, so every feature inherits them. | P0 | M | A-01 | Request-id on every response and log line; the RFC7807 error envelope on every non-2xx; `/health/live` and `/health/ready` behave per spec. |
| A-05 | Structured logging | As an operator, I want JSON logs with redaction, so I can debug without leaking PII. | P0 | S | A-04 | Every request logs the mandated fields; a test asserts that a known password, token and national ID never appear in the log stream. |
| A-06 | Prometheus metrics | As an operator, I want `/metrics`, so I can monitor from day one. | P1 | S | A-04 | The eight metrics in TRD §2.11 are exposed with correct types and labels. |
| A-07 | Frontend scaffold | As a developer, I want the Next.js shell with design tokens, so screens are consistent. | P0 | L | A-01 | App Router, Tailwind tokens matching docs/03 §3.5, shadcn/ui installed, header + sidebar + breadcrumbs render. |
| A-08 | Generated API types | As a frontend developer, I want types generated from OpenAPI, so contract drift is a compile error. | P0 | M | A-04, A-07 | `make types` regenerates `lib/api/generated/`; a deliberate backend field rename breaks `tsc`. |
| A-09 | CI pipeline | As a team, we want CI on every push, so `main` stays deployable. | P0 | L | A-01 | Lint → unit → integration → security → build → e2e; coverage gates enforced; merge blocked on red. |
| A-10 | Global UI states | As a user, I want consistent loading, empty and error states, so the app never shows a blank screen. | P0 | M | A-07 | Skeleton, EmptyState, FilteredEmptyState, ErrorState and Toast components exist and are used by every list built thereafter. |

---

## EPIC B — Identity & Access *(Sprint 1)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| B-01 | Login | As a user, I want to sign in with email and password. | P0 | M | A-02 | Valid credentials return access + refresh + user with permissions; invalid returns 401 with an identical body and timing for unknown-email and wrong-password. |
| B-02 | Password hashing | As a security officer, I want Argon2id hashing with a strong policy. | P0 | S | B-01 | Argon2id with the specified parameters; ≥ 12 chars with complexity; last 5 rejected; plaintext never logged. |
| B-03 | Account lockout | As a security officer, I want brute force stopped. | P0 | S | B-01 | 5 failures in 15 min → 423 for 15 min with `retry_after_seconds`; audit entry written; counter resets on success. |
| B-04 | Token refresh | As a user, I want my session to refresh silently. | P0 | M | B-01 | Refresh rotates both tokens; the old token is marked rotated; the UI refreshes mid-form with no data loss. |
| B-05 | Refresh reuse detection | As a security officer, I want a stolen refresh token to trip an alarm. | P0 | M | B-04 | Presenting a rotated token revokes the whole family, returns 401 and writes `SECURITY_REFRESH_REUSE`. |
| B-06 | Logout | As a user, I want to end my session, everywhere if needed. | P0 | S | B-04 | `/logout` revokes the presented token; `/logout-all` revokes every family; subsequent refresh returns 401. |
| B-07 | RBAC enforcement | As a security officer, I want every endpoint to declare its permission. | P0 | M | B-01, A-03 | `require_permission` on every non-public route; a CI check fails the build if a route lacks one. |
| B-08 | Row scoping | As a security officer, I want users to see only their own scope. | P0 | M | B-07 | A Field Officer requesting another officer's alert receives 404; a branch user sees only their branch's applications. |
| B-09 | Audit service | As an auditor, I want every write recorded transactionally. | P0 | M | A-02 | Audit row commits with the business write or not at all; before/after JSONB captured; redaction applied; `UPDATE` on `audit_logs` raises at the DB level. |
| B-10 | User management | As a Super Admin, I want to create, edit and deactivate users. | P0 | L | B-07 | Create sends a temporary password and forces a change; deactivation revokes all sessions immediately and prompts for alert reassignment. |
| B-11 | Password reset | As a user, I want to reset a forgotten password. | P1 | M | B-01 | Single-use 30-minute token; identical response whether or not the account exists; all other sessions revoked on success. |
| B-12 | Change password | As a user, I want to change my password. | P0 | S | B-01 | Requires the current password; enforces policy; revokes other sessions. |
| B-13 | Role permission viewer | As a Super Admin, I want to see what each role can do. | P1 | S | B-07 | The role screen lists permissions grouped by resource; the user form previews them on role selection. |
| B-14 | Two-factor authentication | As a Super Admin, I want TOTP for privileged roles. | P2 | L | B-01 | TOTP enrolment with 10 hashed recovery codes; mandatory for SA and RM; enrolment and reset audited. |

---

## EPIC C — Route Assessment *(Sprint 2)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| C-01 | Route CRUD API | As a Credit Officer, I want to create and maintain routes. | P0 | L | B-07 | All fields per the data dictionary; every documented validation rule enforced; duplicate (origin, destination, name) → 409; soft delete blocked when an active loan references the route. |
| C-02 | Charging station registry | As an officer, I want stations linked to a corridor. | P1 | M | C-01 | Stations attach to a route with a distance from origin; counts and maximum gap auto-fill from linked stations; `verified_at` staleness surfaced. |
| C-03 | Config service | As the system, I want the active scoring configuration cached and versioned. | P0 | M | A-03 | Exactly one ACTIVE config per type (DB-enforced); cache invalidates on publish; `409 CONFIG_NOT_PUBLISHED` when none exists. |
| C-04 | Normaliser primitives | As a developer, I want BANDED/LINEAR/COMPOSITE rules, so components are data-driven. | P0 | M | C-03 | Interpolation exact at points, below the first and above the last; 500 hypothesis cases stay within 0–100; unknown rule type raises. |
| C-05 | Route scoring engine | As a Credit Officer, I want a 0–100 route score. | P0 | L | C-04 | Kathmandu–Dhulikhel returns exactly 90.12 / Grade A; `Σ weighted == total ± 0.01`; deterministic; pure (a test asserts no DB session is importable). |
| C-06 | Charging adequacy | As a Credit Officer, I want an explicit charging verdict. | P0 | M | C-05 | ADEQUATE/MARGINAL/INADEQUATE per the specified thresholds; INADEQUATE caps the component at 25 and adds a CRITICAL factor. |
| C-07 | Factor generation | As a Credit Officer, I want to see what helped and what hurt. | P0 | M | C-05 | All 17 documented factor rules fire on their conditions with correct severity and interpolated message values. |
| C-08 | Explanation generator | As a Credit Officer, I want a written explanation. | P1 | M | C-07 | A paragraph naming the top contributor, the charging position, demand, margin and seasonal exposure; no placeholder text ever renders. |
| C-09 | Assessment persistence | As an auditor, I want assessments immutable and reproducible. | P0 | M | C-05 | Assessment + components + route denormalisation + audit in one transaction; `UPDATE` raises; `input_snapshot` replays to the identical score. |
| C-10 | Assess endpoints | As a Credit Officer, I want to run and re-run assessments. | P0 | M | C-09 | `POST /routes/{id}/assess` returns 201 in < 2 s; idempotency key honoured; 422 lists every missing field and writes nothing. |
| C-11 | Route list screen | As a user, I want to find routes fast. | P0 | L | C-01 | Server-side pagination, sorting and 8 filters; filter state in the URL; empty and filtered-empty states distinct; < 400 ms with 500 routes. |
| C-12 | Route form screen | As a Credit Officer, I want to enter route data with live feedback. | P0 | L | C-01 | Six sections; inline validation with per-section error counts; live derived panel; autosave every 20 s; unsaved-changes guard. |
| C-13 | Route score result screen | As a Credit Officer, I want the score fully explained on one screen. | P0 | L | C-10 | Gauge, recommendation card, five expandable component bars, factor lists, explanation, charging strip; grade region distinguishable in greyscale. |
| C-14 | Route detail screen | As a user, I want the full route record. | P0 | L | C-10 | Five tabs; assessment history with a score-over-time chart; linked loans with exposure; activity trail. |
| C-15 | Staleness flagging | As a Risk Manager, I want old assessments flagged. | P1 | S | C-09 | Assessments older than the configured window show a "Stale" chip in the list and a banner on the result; assessment is blocked from reuse in an application unless re-run or explicitly accepted. |
| C-16 | Assessment comparison | As a Risk Manager, I want to compare two assessments. | P2 | M | C-14 | Side-by-side component diff with deltas and a summary of what changed. |
| C-17 | Route PDF export | As a Credit Officer, I want a PDF for the credit file. | P2 | M | C-13 | PDF mirrors the screen, includes config version, engine version, assessor and timestamp. |

---

## EPIC D — Applicant Management *(Sprint 3)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| D-01 | Applicant CRUD | As a Credit Officer, I want to register applicants of six types. | P0 | L | B-07 | Type-conditional required fields enforced server-side; age 18–70; Nepal phone pattern; licence expiry in the future. |
| D-02 | Identifier encryption | As a security officer, I want national IDs encrypted and masked. | P0 | M | D-01 | `id_number_enc` encrypted with pgcrypto; responses masked by default; unmasking requires `applicant:read_sensitive` and is audited per view. |
| D-03 | Duplicate detection | As a Credit Officer, I want to be warned about duplicates. | P0 | S | D-02 | Hash match returns 409 with the existing applicant id; the UI offers "Open existing" or "Continue anyway" (the latter requires a reason and is audited). |
| D-04 | Financial profile versioning | As an auditor, I want to know which financial data a decision used. | P0 | L | D-01 | Saving creates a new version and flips `is_current`; exactly one current per applicant (DB-enforced); applications snapshot the version id. |
| D-05 | Existing obligations | As a Credit Officer, I want obligations captured and totalled. | P0 | M | D-04 | Sub-table CRUD; totals derived and not directly settable; CIB-sourced rows read-only and marked. |
| D-06 | Document upload | As a Credit Officer, I want to attach documents. | P0 | M | D-01 | Magic-byte + MIME allow-list + 10 MB cap; stored under a UUID key outside the web root; checksum recorded; download authenticated and audited. |
| D-07 | Mock CIB provider | As a developer, I want deterministic bureau data for demos and tests. | P0 | M | D-01 | The same ID always returns the same report; the seeded population spans the full score range including a blacklisted and a defaulted case. |
| D-08 | CIB adapter + caching | As the system, I want a swappable, resilient bureau integration. | P0 | M | D-07 | One interface, two implementations selected by env var; cached within the validity window; 3 retries then circuit-open → 503 with the manual-entry path. |
| D-09 | Manual bureau entry | As a Credit Officer, I want to proceed when the bureau is down. | P1 | M | D-08 | Manual entry captures the same fields, is flagged `is_manual_entry`, is visibly marked on the assessment, and blocks automatic APPROVE. |
| D-10 | Customer list screen | As a user, I want to find customers. | P0 | M | D-01 | Search by name, code, phone and masked ID; six filters; card layout below 1024px. |
| D-11 | Add customer wizard | As a Credit Officer, I want guided registration. | P0 | L | D-01 | Three steps; type drives conditional fields; duplicate warning on blur; progress preserved on reload. |
| D-12 | Customer profile screen | As a user, I want a 360° view. | P0 | L | D-04 | Summary rail with 8 metrics; seven tabs; blacklist banner disables New Application with an explanation. |
| D-13 | Financial info screen | As a Credit Officer, I want live affordability feedback while typing. | P0 | L | D-04 | Live disposable income, DTI and the "maximum affordable EMI" line; expenses > income warns without blocking; version history diff available. |
| D-14 | Credit assessment screen | As a Credit Officer, I want the bureau report and knock-outs on one screen. | P0 | L | D-08 | Score dial, 24-month repayment strip with legend and tooltips, knock-out checklist with value vs threshold, fetch/refresh with cache indication. |

---

## EPIC E — Credit Scoring & Underwriting *(Sprints 3–4)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| E-01 | Knock-out engine | As a Risk Manager, I want hard rules to stop a file immediately. | P0 | M | D-08 | All 10 knock-outs configurable; any hit returns score 0, grade E, REJECT with the code, and performs no component scoring. |
| E-02 | Customer scoring engine | As a Credit Officer, I want a 0–100 customer score. | P0 | L | C-04, D-08 | Six components; experience sub-factors switch by applicant type; the reference borrower returns exactly 75.17 / Grade B; thin file scores 45 with a factor. |
| E-03 | Affordability calculators | As a Credit Officer, I want DTI, FOIR and disposable income. | P0 | S | D-04 | Values match the documented formulas to 4 decimal places; division by zero income returns a validation error, not infinity. |
| E-04 | Vehicle economics | As a Credit Officer, I want the asset's own earning power quantified. | P0 | M | E-03 | Energy cost per km, daily and monthly net contribution, DSCR and payback match the worked example; terrain and seasonal factors applied. |
| E-05 | Combined risk engine | As a Credit Officer, I want one final score. | P0 | M | C-05, E-02, E-04 | Weighted per the FINAL config; the reference case returns 84.31 / Grade B; weights validated to 100%. |
| E-06 | Loan structuring | As a Credit Officer, I want a recommended structure, not just a score. | P0 | L | E-05 | Amount is the minimum of requested, LTV cap, EMI capacity and product max; tenure capped by grade and battery warranty; rate = base + grade premium; below product minimum → REJECT. |
| E-07 | EMI calculator | As the system, I want exact instalment arithmetic. | P0 | M | — | Reference EMI matches to ±0.50; zero-rate handled; `Decimal` throughout; no `float` anywhere (type-checked). |
| E-08 | Decision matrix | As a Risk Manager, I want deterministic, configurable decisions. | P0 | M | E-06 | First-match-wins over the four rows; every parameter read from `decision_rules`; boundary cases tested at 49.99/50.00 and DSCR 0.99/1.00. |
| E-09 | Reason generation | As a Credit Officer, I want to know exactly why. | P0 | M | E-08 | Reasons ordered negatives-first by impact; each carries a code, message, metric value and threshold; no decision is ever returned without reasons. |
| E-10 | Assessment orchestration | As a Credit Officer, I want one button to run everything. | P0 | L | E-08 | `POST /applications/{id}/assess` runs the six-stage pipeline in one transaction; persists config and engine versions and input snapshots; p95 < 1.5 s. |
| E-11 | Decision recording | As a Risk Manager, I want to record and override decisions. | P0 | M | E-10 | Override requires `application:override` and a ≥ 20-character justification; approved amount cannot breach the LTV cap; state transition validated; audited. |
| E-12 | Application CRUD + wizard API | As a Credit Officer, I want to assemble an application incrementally. | P0 | L | D-04, C-01 | Draft persists partially; status machine enforced; financial profile and vehicle price snapshotted at creation. |
| E-13 | Application wizard UI | As a Credit Officer, I want a guided six-step form. | P0 | L | E-12 | Per-step validation; economics preview on vehicle selection; route warnings for Class C and stale assessments; autosave and resume. |
| E-14 | Underwriting result screen | As a Credit Officer, I want the decision and its evidence on one screen. | P0 | L | E-10 | Decision banner, three contribution cards, reason list, requested-vs-recommended table; knock-out case collapses to a rejection card. |
| E-15 | What-if simulator | As a Credit Officer, I want to find a structure that works. | P1 | M | E-14 | Amount, tenure and down-payment sliders recompute EMI, DSCR, FOIR and LTV via the stateless endpoint, debounced, marked indicative until saved. |
| E-16 | Decision modal | As a Risk Manager, I want to record a decision with conditions. | P0 | M | E-11 | Pre-filled from the recommendation; condition library plus free text; override warning banner; justification counter. |
| E-17 | Stateless scoring endpoints | As a frontend developer, I want to score without persisting. | P0 | S | E-05 | `/scoring/route`, `/scoring/customer`, `/scoring/final`, `/scoring/emi` return identical arithmetic to the persisted path. |
| E-18 | Four-eyes approval | As a Risk Manager, I want a second approver above a threshold. | P2 | L | E-11 | Above the configured amount, a second distinct approver is required before the application becomes APPROVED. |
| E-19 | Credit appraisal PDF | As a Credit Officer, I want a memo for the file. | P2 | L | E-14 | PDF includes all three scores, components, reasons, structure, decision and approver. |

---

## EPIC F — Loan Management *(Sprint 4)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| F-01 | Loan booking | As a Credit Officer, I want to book an approved loan. | P0 | M | E-11 | Only from APPROVED; one loan per application (DB-enforced); idempotency key prevents duplicates; vehicle → FINANCED, application → DISBURSED. |
| F-02 | Amortisation schedule | As the system, I want an exact repayment schedule. | P0 | L | E-07, F-01 | `Σ principal_due == principal` exactly; final closing balance 0.00; due dates handle month-end correctly; generated atomically with the loan. |
| F-03 | Repayment posting | As a Portfolio Manager, I want to record payments. | P0 | L | F-02 | Allocation penalty → interest → principal, oldest instalment first; partial, full, multi-instalment and advance all handled; idempotent; loan totals recomputed in the same transaction. |
| F-04 | DPD & classification job | As a Risk Manager, I want daily arrears position. | P0 | M | F-03 | Nightly recompute of DPD, outstanding, overdue and classification per the DPD bucket table; idempotent per `as_of_date`. |
| F-05 | Loan detail screen | As a Portfolio Manager, I want the full loan record. | P0 | L | F-02 | Six summary tiles; the original underwriting snapshot displayed read-only with links to the archived assessments; seven tabs. |
| F-06 | Repayment schedule screen | As a Portfolio Manager, I want the instalment view and payment entry. | P0 | L | F-03 | Overdue and next-due rows visually distinguished by border and chip, not colour alone; payment modal shows a live allocation preview. |
| F-07 | Loan restructuring | As a Risk Manager, I want to restructure a stressed loan. | P2 | L | F-02 | New schedule generated; the original retained and viewable; audit records the reason; classification handled per policy. |
| F-08 | Foreclosure & settlement | As a Portfolio Manager, I want to close a loan early. | P2 | M | F-03 | Settlement amount computed; loan closed with the correct status; schedule rows marked. |

---

## EPIC G — Portfolio Monitoring *(Sprint 5)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| G-01 | Mock telematics provider | As a developer, I want realistic vehicle data for demos and tests. | P0 | L | F-01 | Profile-driven daily series per vehicle; injectable distress scenarios; deterministic per seed; the demo loan shows usage decline preceding payment failure. |
| G-02 | Telemetry ingestion job | As the system, I want daily vehicle data persisted. | P0 | L | G-01 | Validated (km ≤ 800, hours ≤ 24, no future dates); bulk upsert into the right monthly partition; vehicles with no data flagged `MONITORING_DEGRADED`; idempotent. |
| G-03 | Battery & charging ingestion | As the system, I want battery and charging data. | P0 | M | G-02 | State of charge, state of health, sessions and energy persisted; efficiency derived; out-of-range values rejected and counted. |
| G-04 | Usage baseline job | As the system, I want rolling baselines and change percentages. | P0 | L | G-02 | 7/30/90-day averages, baseline after 30 days on book, change %, active days, zero-km streak, revenue and charging change, all in `loan_monitoring_snapshots`; null baseline before 30 days. |
| G-05 | Behaviour score | As a Portfolio Manager, I want one ranking number per loan. | P1 | S | G-04 | Five weighted components per spec; band assigned; used as a sortable column. |
| G-06 | Partition maintenance | As an operator, I want partitions created ahead of time. | P0 | S | A-02 | A monthly job creates three months ahead; a test inserting a future-dated row lands in the correct partition. |
| G-07 | Job framework | As an operator, I want jobs tracked and alertable. | P0 | M | A-06 | Every job writes a `job_runs` row and updates `job_last_success_timestamp`; unique index makes a same-date success non-repeatable; failures recorded with the error. |
| G-08 | Portfolio dashboard | As a Portfolio Manager, I want the book at a glance. | P0 | L | F-04 | Six KPIs, three charts and the risk table; ten filters; four saved-view chips; RED rows visually distinguished by border and icon. |
| G-09 | Risk table | As a Portfolio Manager, I want to triage by risk. | P0 | L | G-04 | All 12 columns; server-side sort on 5 columns; usage sparkline per row; aggregates returned with the page. |
| G-10 | Vehicle monitoring screen | As a Portfolio Manager, I want to see how the asset is operating. | P0 | L | G-04 | Six metric tiles, five charts with threshold reference lines, maintenance timeline, data-quality panel; a clear empty state when no device is linked. |
| G-11 | Customer behaviour screen | As a Portfolio Manager, I want repayment and usage on one timeline. | P0 | L | G-04 | The combined chart overlays EMI events on the daily-km line; punctuality strip; route-peer comparison; alert history. |
| G-12 | Maintenance events | As a Field Officer, I want to log breakdowns and downtime. | P1 | S | F-01 | CRUD with type, cost, downtime days; downtime feeds availability and appears on the monitoring timeline. |

---

## EPIC H — Early Warning & Alerts *(Sprint 5)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| H-01 | Metric registry | As a developer, I want to add a signal without changing the engine. | P0 | L | G-04 | 19 metrics registered; adding one requires only a provider function and a seed row; a null metric never fires a condition. |
| H-02 | Rule engine core | As a Risk Manager, I want rules evaluated deterministically. | P0 | L | H-01 | Pure evaluation over a `MetricSnapshot`; 8 operators; ALL/ANY logic; all 15 documented test cases pass without a database. |
| H-03 | Alert creation & dedup | As a Portfolio Manager, I want one alert per problem. | P0 | L | H-02 | Unique partial index prevents a second live alert for the same (loan, rule); a repeat evaluation increments `occurrence_count`; suppression window honoured. |
| H-04 | Supersession | As a Portfolio Manager, I want escalation, not duplication. | P0 | M | H-03 | A higher-severity rule in the same family closes the lower one as SUPERSEDED with the link preserved. |
| H-05 | Auto-resolution | As a Portfolio Manager, I want cleared conditions to close themselves. | P0 | M | H-03 | Condition false + `auto_resolve` → RESOLVED with `AUTO_CONDITION_CLEARED`, an activity note and an audit entry. |
| H-06 | Alert lifecycle API | As a Portfolio Manager, I want to work an alert. | P0 | M | H-03 | Acknowledge, assign, add activity, resolve, escalate; transitions validated; resolution notes minimum lengths enforced; optimistic locking returns 409 on a stale version. |
| H-07 | SLA & escalation job | As a Risk Manager, I want breaches escalated automatically. | P0 | M | H-06 | Hourly job moves breached alerts to ESCALATED, reassigns to the supervisor role and notifies; escalation count is a dashboard metric. |
| H-08 | Notifications | As a Portfolio Manager, I want to be told about RED alerts. | P1 | M | H-03 | In-app always; email for RED; retries with backoff; failure recorded and visible, never blocking the business transaction. |
| H-09 | Alert queue screen | As a Portfolio Manager, I want a prioritised work queue. | P0 | L | H-06 | Severity-then-age default sort; five tabs; SLA chip with three states; bulk assign; positive empty state when nothing is open. |
| H-10 | Alert detail screen | As a Field Officer, I want everything I need to act. | P0 | L | H-06 | Trigger evidence with value vs threshold, supporting context charts, recommended action, activity composer, resolution modal; fully usable at 375px. |
| H-11 | On-demand evaluation | As a Risk Manager, I want to re-evaluate a loan now. | P1 | S | H-02 | `POST /alerts/evaluate` returns 202 with a job id; single-loan and whole-book modes. |
| H-12 | Rule dry-run | As a Risk Manager, I want to test a rule before enabling it. | P2 | L | H-02 | Runs against 90 days of snapshots and reports how many alerts it would have raised, with a sample. |

---

## EPIC I — Dashboards & Reporting *(Sprint 6)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| I-01 | Materialised views | As a developer, I want fast aggregates. | P0 | M | F-04 | Five views with unique indexes; `REFRESH CONCURRENTLY` never blocks readers; refreshed nightly and after booking/repayment. |
| I-02 | Dashboard API | As a user, I want one call for my dashboard. | P0 | L | I-01 | All 10 KPIs and 9 chart datasets; `data_as_of` returned; p95 < 400 ms with 10k loans. |
| I-03 | Main dashboard screen | As a user, I want role-appropriate situational awareness. | P0 | L | I-02 | 10 KPI cards linking to filtered lists; 7 charts (MVP); attention table; per-widget error states; first-run onboarding state. |
| I-04 | Role dashboard variants | As each role, I want my own KPIs. | P1 | M | I-03 | Five documented variants render the specified widget sets. |
| I-05 | Risk report | As a Risk Manager, I want a portfolio risk pack. | P1 | L | I-01 | Grade distribution with exposure and PAR, grade migration, route concentration, top 20 exposures, alert statistics, override list; CSV export. |
| I-06 | Portfolio report | As a Portfolio Manager, I want a loan schedule report. | P1 | M | I-01 | Loan-level rows, ageing summary, disbursement and collection by month; CSV export. |
| I-07 | Application report | As a Risk Manager, I want funnel analytics. | P1 | M | I-01 | Funnel with conversion, decisions by officer, time-to-decision, ranked rejection reasons; CSV export. |
| I-08 | Export audit | As an auditor, I want exports recorded. | P0 | S | I-05 | Every export writes an audit entry with the filter set and row count; the file is watermarked with user and timestamp; rate-limited. |
| I-09 | Geographic distribution | As a Risk Manager, I want exposure by province. | P2 | M | I-01 | Ranked bar or map with outstanding, overdue and NPL count per district. |
| I-10 | Scheduled report email | As a Risk Manager, I want a weekly pack by email. | P3 | M | I-05 | Configurable recipients and schedule; attachment generated by a job. |

---

## EPIC J — Administration *(Sprint 6)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| J-01 | Scoring config API | As a Risk Manager, I want to draft and publish scorecards. | P0 | L | C-03 | Draft/edit/publish/archive; weights validated to exactly 1.0000 at publish (DB + API); only drafts editable; publish archives the prior ACTIVE atomically. |
| J-02 | Scoring config screen | As a Risk Manager, I want to change weights without an engineer. | P0 | L | J-01 | Sliders bound to numeric inputs; live total indicator blocks publish off 100%; grade band editor with overlap detection; curve editor with a live preview chart. |
| J-03 | Publish diff & change note | As an auditor, I want to know what changed and why. | P0 | M | J-01 | The publish modal shows every changed weight and threshold; a change note is mandatory and stored on the version; audited. |
| J-04 | Risk rules API | As a Risk Manager, I want to manage alert rules. | P0 | L | H-02 | CRUD over rules and conditions; toggling off auto-resolves live alerts with `RULE_DEACTIVATED`; every change audited with a before/after condition set. |
| J-05 | Risk rules screen | As a Risk Manager, I want a plain-language rule builder. | P0 | L | J-04 | Condition builder renders the rule as a readable sentence; metric dropdown from the registry; disable confirm names the affected open alerts. |
| J-06 | System settings | As a Risk Manager, I want to change loan and underwriting policy. | P0 | L | B-07 | Seven groups; each setting validated by type and bounds; the save modal lists `key: old → new`; each change audited individually. |
| J-07 | Audit log screen | As an auditor, I want to search and inspect history. | P0 | L | B-09 | Seven filters; before/after JSON diff with redaction; read-only with no mutation affordances; CSV export watermarked and audited. |
| J-08 | Config preview/simulation | As a Risk Manager, I want to see the impact before publishing. | P2 | XL → split | J-01 | Runs a draft over N recent applications and reports the grade/decision migration matrix. |
| J-09 | Config rollback | As a Risk Manager, I want to revert to a prior version. | P2 | S | J-01 | Cloning an archived version to a draft and publishing restores the prior behaviour exactly; verified by re-scoring a stored snapshot. |
| J-10 | Job monitoring screen | As an operator, I want to see job health. | P1 | S | G-07 | Recent runs with status, duration, counts and errors; manual trigger with an `as_of_date`. |

---

## EPIC K — Quality, Security & Release *(Sprint 7)*

| ID | Feature | User story | Pri | Cx | Depends | Acceptance criteria |
|----|---------|-----------|-----|----|---------|---------------------|
| K-01 | Authorisation matrix test | As a security officer, I want authorisation proven, not assumed. | P0 | M | B-07 | Every route enumerated from OpenAPI × six roles asserted against the declared matrix; a route missing its permission fails the build. |
| K-02 | E2E suite | As a team, we want critical paths protected. | P0 | L | all | All 14 specs green on three consecutive runs, including the full 17-step demo flow. |
| K-03 | Security test suite | As a security officer, I want the OWASP basics covered. | P0 | L | all | Injection, XSS, IDOR, mass assignment, upload, token tampering, rate limit, header and log-leak tests all pass. |
| K-04 | Performance testing | As an operator, I want the targets verified. | P0 | L | all | k6 scenarios meet the documented p95 targets with 100k applications / 10k loans / 3M telemetry rows. |
| K-05 | Accessibility audit | As a user with a disability, I want to use the product. | P0 | M | all | axe clean (no critical/serious) on the ten most-used screens; the demo flow completable by keyboard only and by screen reader. |
| K-06 | Production images & Nginx | As an operator, I want a hardened deployment. | P0 | M | A-01 | Multi-stage non-root images pinned by digest; TLS, HSTS and all security headers verified by an external scanner. |
| K-07 | Backup & restore | As an operator, I want provable recovery. | P0 | M | K-06 | Nightly encrypted backup with off-site copy; a timed restore drill completes within the 8-hour RTO and is signed off. |
| K-08 | Monitoring & alerting | As an operator, I want to know before users tell me. | P0 | M | A-06 | Prometheus, Grafana dashboards and six alert rules live; verified by inducing a failure. |
| K-09 | Runbooks | As an operator, I want documented procedures. | P0 | M | K-07 | Deployment, rollback, DR and incident runbooks written, reviewed and available offline. |
| K-10 | Demo data pack | As a presenter, I want a reliable demo. | P0 | M | K-02 | `make demo-reset` restores the full pack in < 30 s with dates realigned so alert conditions are ripe. |
| K-11 | User documentation & training | As a user, I want to learn the system. | P1 | M | K-02 | A role-specific guide and a 45-minute walkthrough per role; UAT sign-off recorded. |
| K-12 | Go-live | As a business, we want it in production. | P0 | M | all | Every §9.16 checklist item ticked; zero S1/S2 defects; deployed, smoke-tested and handed over. |

---

## Sprint allocation summary

| Sprint | Weeks | Epics | Stories | P0 |
|--------|-------|-------|---------|-----|
| 1 | 1–2 | A, B | 24 | 20 |
| 2 | 3–4 | C | 17 | 13 |
| 3 | 5–6 | D, E (part) | 20 | 17 |
| 4 | 7–8 | E (rest), F | 15 | 12 |
| 5 | 9–10 | G, H | 24 | 20 |
| 6 | 11–12 | I, J | 20 | 12 |
| 7 | 13–14 | K | 12 | 10 |

*Story counts exceed 96 because several epics span two sprints; P2/P3 items are listed with their
epic but scheduled post-MVP.*

---

## Backlog hygiene rules

1. **XL is not a size, it is a warning.** Any story estimated XL is split before it enters a sprint.
   The four XL stories in this backlog (C-13, D-14, E-14, J-02 in their original form) already carry
   their split points in [11-MVP-ROADMAP.md](11-MVP-ROADMAP.md).
2. **No story without acceptance criteria.** A story lacking testable criteria is not ready.
3. **A new P0 displaces an existing P0.** Only the PM changes priority, and the trade is explicit.
4. **Every P0 has a test.** The Definition of Done in [10-TESTING-STRATEGY.md](10-TESTING-STRATEGY.md)
   §10.9 applies to all of them.
5. **Engine stories carry a documentation obligation.** A change to scoring, rules or the API updates
   the corresponding `docs/` file in the same pull request — the worked examples in
   [07-SCORING-ENGINE.md](07-SCORING-ENGINE.md) are executable tests, so drift breaks the build.
