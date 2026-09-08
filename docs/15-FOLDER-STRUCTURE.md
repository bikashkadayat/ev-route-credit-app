# 15. Project Folder Structure & Conventions

**Stack:** Next.js + TypeScript (frontend) · FastAPI + Python (backend) · PostgreSQL
**Repository model:** single repository, two applications, shared contracts.

---

## 15.1 Top level

```
ev-rca/
├── README.md
├── Makefile                        # one-word commands: up, down, test, seed, demo-reset, lint
├── docker-compose.yml              # local: web, api, db, nginx
├── docker-compose.prod.yml         # production overlay: + redis, celery, prometheus, grafana
├── .env.example                    # every variable documented, no values
├── .gitignore                      # .env*, __pycache__, node_modules, .next, coverage
├── .pre-commit-config.yaml         # ruff, black, mypy, eslint, prettier, gitleaks
├── .github/
│   └── workflows/
│       ├── ci.yml                  # lint → unit → integration → security → build → e2e
│       ├── nightly.yml             # k6 performance, ZAP baseline, full-browser e2e
│       └── deploy.yml              # staging on main; production on tag with manual approval
├── backend/                        # FastAPI application
├── frontend/                       # Next.js application
├── db/                             # schema, migrations reference, seeds
├── docs/                           # this documentation set
├── api-examples/                   # request/response JSON per endpoint
├── infra/
│   ├── nginx/
│   │   ├── nginx.conf
│   │   └── ssl/                    # certificates (gitignored)
│   ├── prometheus/prometheus.yml
│   ├── grafana/dashboards/
│   └── scripts/
│       ├── backup.sh
│       ├── restore.sh
│       └── demo-reset.sh
└── tests/
    └── e2e/                        # Playwright specs (cross-cutting, run against the full stack)
```

---

## 15.2 Backend — `backend/`

```
backend/
├── Dockerfile                      # multi-stage, non-root, pinned base digest
├── pyproject.toml                  # deps, ruff, black, mypy, pytest config
├── poetry.lock
├── alembic.ini
├── app/
│   ├── main.py                     # FastAPI app factory, middleware chain, router registration
│   ├── cli.py                      # typer CLI: seed, run-job, create-partitions, demo-reset
│   │
│   ├── core/                       # cross-cutting, no domain knowledge
│   │   ├── config.py               # pydantic-settings; every env var typed
│   │   ├── database.py             # async engine, session factory, base class
│   │   ├── security.py             # argon2, JWT encode/decode, token generation
│   │   ├── dependencies.py         # get_current_user, require_permission, get_db, pagination
│   │   ├── exceptions.py           # typed domain exceptions + HTTP mapping
│   │   ├── middleware.py           # request-id, audit context, timing, metrics
│   │   ├── logging.py              # structlog config + the redaction processor
│   │   ├── pagination.py
│   │   └── idempotency.py
│   │
│   ├── models/                     # SQLAlchemy ORM — one file per aggregate, mirrors the schema
│   │   ├── base.py                 # Base, TimestampMixin, SoftDeleteMixin, AuditMixin
│   │   ├── user.py                 # User, Role, Permission, RolePermission, UserSession
│   │   ├── applicant.py            # Applicant, ApplicantFinancial, ExistingObligation,
│   │   │                           #   ApplicantDocument, CreditBureauReport
│   │   ├── vehicle.py              # VehicleModel, Vehicle, MaintenanceEvent
│   │   ├── route.py                # Route, ChargingStation, RouteAssessment, RouteScoreComponent
│   │   ├── scoring.py              # ScoringConfiguration, ScoringConfigComponent
│   │   ├── application.py          # LoanApplication, CreditScore, CreditScoreComponent,
│   │   │                           #   LoanAssessment, UnderwritingDecision
│   │   ├── loan.py                 # Loan, RepaymentSchedule, Repayment
│   │   ├── telemetry.py            # VehicleTelemetry, BatteryMetric, ChargingSession,
│   │   │                           #   LoanMonitoringSnapshot
│   │   ├── risk.py                 # RiskRule, RiskRuleCondition, RiskAlert, AlertActivity
│   │   └── system.py               # AuditLog, Notification, SystemSetting, JobRun
│   │
│   ├── schemas/                    # Pydantic v2 — the API contract
│   │   ├── common.py               # Page[T], ErrorResponse, Money, ScoreValue
│   │   ├── auth.py  applicant.py  vehicle.py  route.py  scoring.py
│   │   ├── application.py  loan.py  monitoring.py  risk.py  dashboard.py  admin.py
│   │
│   ├── engines/                    # ★ PURE — no I/O, no clock, no DB. The heart of the product.
│   │   ├── base.py                 # ScoringEngine ABC, ComponentResult, ScoreResult, Factor
│   │   ├── normalizer.py           # BANDED / LINEAR / COMPOSITE primitives
│   │   ├── factors.py              # declarative factor rule evaluator
│   │   ├── explain.py              # narrative generation from components + factors
│   │   ├── route_engine.py         # route-engine@1.0.0
│   │   ├── customer_engine.py      # customer-engine@1.0.0
│   │   ├── knockouts.py            # hard eligibility rules
│   │   ├── vehicle_economics.py    # energy, contribution, DSCR, payback
│   │   ├── final_engine.py         # final-engine@1.0.0
│   │   ├── structuring.py          # LTV caps, EMI capacity, tenure, pricing
│   │   ├── decision_matrix.py      # APPROVE / MANUAL_REVIEW / REJECT + reasons
│   │   └── emi.py                  # EMI + amortisation schedule, Decimal-exact
│   │
│   ├── repositories/               # the only place SQLAlchemy queries live
│   │   ├── base.py                 # generic CRUD, soft delete, pagination
│   │   └── user.py  applicant.py  route.py  application.py  loan.py
│   │       telemetry.py  risk.py  dashboard.py  audit.py
│   │
│   ├── services/                   # use cases; own transactions; emit audit
│   │   ├── auth_service.py  user_service.py  rbac_service.py
│   │   ├── route_service.py  route_assessment_service.py
│   │   ├── applicant_service.py  financial_service.py  document_service.py
│   │   ├── application_service.py  underwriting_service.py
│   │   ├── loan_service.py  schedule_service.py  repayment_service.py
│   │   ├── telemetry_service.py  baseline_service.py
│   │   ├── alert_service.py  config_service.py  settings_service.py
│   │   ├── dashboard_service.py  report_service.py
│   │   ├── audit_service.py  notification_service.py
│   │
│   ├── risk/                       # the rule engine (pure core + orchestration)
│   │   ├── metrics.py              # metric provider registry (19 providers)
│   │   ├── snapshot.py             # set-based MetricSnapshot builder
│   │   ├── engine.py               # pure condition/rule evaluation
│   │   └── families.py             # signal families and supersession map
│   │
│   ├── integrations/               # adapters — mock and live behind one interface
│   │   ├── cib/  base.py  mock.py  live.py  schemas.py
│   │   ├── telematics/  base.py  mock.py  live.py  schemas.py
│   │   ├── charging/  base.py  registry.py
│   │   └── notifications/  base.py  smtp.py  in_app.py
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── router.py           # aggregates all routers under /api/v1
│   │       └── endpoints/
│   │           ├── auth.py  users.py  routes.py  applicants.py  vehicles.py
│   │           ├── applications.py  scoring.py  loans.py  portfolio.py
│   │           ├── alerts.py  dashboard.py  reports.py  admin.py  health.py
│   │
│   └── jobs/                       # idempotent, CLI-invokable, Celery-ready
│       ├── base.py                 # job_runs wrapper, as_of_date handling, metrics
│       ├── ingest_telemetry.py     recompute_dpd.py     rebuild_baselines.py
│       ├── evaluate_rules.py       escalate_alerts.py   dispatch_notifications.py
│       ├── refresh_views.py        create_partitions.py purge_expired.py
│
├── migrations/                     # Alembic
│   ├── env.py
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_partitions_and_views.py
│       └── ...
│
└── tests/
    ├── conftest.py                 # fixtures: db, client, tokens per role, factories
    ├── factories/                  # factory-boy, deterministic
    ├── unit/
    │   ├── engines/                # ★ the deepest tests: reference cases + hypothesis
    │   ├── risk/                   # the 15 rule-engine cases
    │   └── services/
    ├── integration/
    │   ├── api/                    # one module per endpoint group
    │   ├── db/                     # trigger, constraint and partition behaviour
    │   └── jobs/
    └── security/
        ├── test_authorization_matrix.py   # ★ every endpoint × every role
        ├── test_injection.py  test_idor.py  test_upload.py  test_headers.py
```

**Import rules (enforced in CI by `ruff` isort sections + a custom check):**

```
api/       → services, schemas, core
services/  → repositories, engines, risk, integrations, models, schemas, core
engines/   → schemas (DTOs only), core.types          ← NEVER models, repositories, integrations
risk/      → schemas, core                            ← engine.py NEVER imports a session
repositories/ → models, core
models/    → core
```

A pull request that makes `engines/` import a database session fails the build. That single rule is
what keeps the scoring deterministic and testable.

---

## 15.3 Frontend — `frontend/`

```
frontend/
├── Dockerfile
├── package.json
├── tsconfig.json                   # strict: true, noUncheckedIndexedAccess: true
├── next.config.mjs
├── tailwind.config.ts              # design tokens from docs/03 §3.5
├── playwright.config.ts
├── vitest.config.ts
├── public/
│   ├── fonts/                      # Inter, Noto Sans Devanagari (self-hosted)
│   └── illustrations/              # empty-state line art
└── src/
    ├── app/
    │   ├── layout.tsx              # html, fonts, providers
    │   ├── globals.css             # tokens, resets
    │   ├── (auth)/                 # unauthenticated route group
    │   │   ├── layout.tsx          # split-screen brand layout
    │   │   ├── login/page.tsx
    │   │   ├── forgot-password/page.tsx
    │   │   └── reset-password/page.tsx
    │   └── (app)/                  # authenticated route group
    │       ├── layout.tsx          # session guard, header, sidebar
    │       ├── dashboard/page.tsx
    │       ├── routes/
    │       │   ├── page.tsx  new/page.tsx
    │       │   └── [id]/  page.tsx  edit/page.tsx  assessments/[aid]/page.tsx
    │       ├── customers/
    │       │   ├── page.tsx  new/page.tsx
    │       │   └── [id]/  page.tsx  financials/page.tsx
    │       ├── applications/
    │       │   ├── page.tsx  new/page.tsx
    │       │   └── [id]/  page.tsx  credit/page.tsx  result/page.tsx
    │       ├── loans/[id]/  page.tsx  schedule/page.tsx  monitoring/page.tsx  behaviour/page.tsx
    │       ├── portfolio/page.tsx
    │       ├── alerts/  page.tsx  [id]/page.tsx
    │       ├── reports/  risk/  portfolio/  applications/
    │       └── admin/  users/  scoring/  risk-rules/  settings/  audit-logs/
    │
    ├── components/
    │   ├── ui/                     # shadcn primitives, owned in-repo
    │   │   button.tsx  input.tsx  select.tsx  dialog.tsx  table.tsx  badge.tsx
    │   │   tabs.tsx  toast.tsx  skeleton.tsx  date-picker.tsx  slider.tsx
    │   ├── layout/                 # AppShell, Header, Sidebar, Breadcrumbs, CommandPalette
    │   ├── data/                   # DataTable, Pagination, FilterBar, EmptyState,
    │   │                           #   ErrorState, LoadingSkeleton, ExportButton
    │   ├── charts/                 # ScoreGauge, ComponentBar, TrendLine, DonutChart,
    │   │                           #   StackedBar, UsageChart, RepaymentStrip, ChargingStrip,
    │   │                           #   ChartContainer (handles empty/loading/table-toggle)
    │   ├── risk/                   # ScoreBadge, RiskPill, GradeChip, FactorList,
    │   │                           #   ReasonList, DecisionBanner, SlaChip
    │   ├── forms/                  # FormField, CurrencyInput, PercentInput, NepaliDateInput,
    │   │                           #   AddressFields, ObligationsTable, WizardStepper
    │   └── features/               # screen-specific composites
    │       route-assessment/  credit-assessment/  underwriting/  monitoring/
    │       alerts/  admin-scoring/  admin-rules/
    │
    ├── lib/
    │   ├── api/
    │   │   ├── generated/          # ★ openapi-typescript output — never edited by hand
    │   │   ├── client.ts           # fetch wrapper: auth, refresh, request-id, error mapping
    │   │   └── hooks/              # useRoutes, useAssessRoute, useApplication, useAlerts, ...
    │   ├── validation/             # Zod schemas mirroring Pydantic, one per form
    │   ├── format/                 # formatNPR, formatPercent, formatDate, toBikramSambat
    │   ├── permissions.ts          # usePermission, <Can>
    │   ├── constants/              # enums, grade colours, chart palettes, nepal-admin-units
    │   └── utils/                  # cn, debounce, download, error helpers
    │
    ├── hooks/                      # useAuth, useDebounce, useAutosave, useMediaQuery, useToast
    ├── stores/                     # zustand: ui-store (sidebar, density, saved filters) only
    ├── types/                      # hand-written types that are not API-generated
    └── __tests__/                  # colocated component and lib tests
```

**Frontend rules**

- `lib/api/generated/` is regenerated by `make types` from the running API. Editing it by hand is a
  review rejection.
- Every form has exactly one Zod schema in `lib/validation/`, and the same schema types the payload.
- Server Components for read-heavy pages (lists, dashboards); Client Components for forms, charts and
  tables. A component becomes `"use client"` only when it needs interactivity.
- Colour is never applied inline. Risk colour comes from `<RiskPill>` / `<ScoreBadge>` only, so the
  mapping exists in exactly one place.
- No `dangerouslySetInnerHTML`, enforced by ESLint.

---

## 15.4 Database — `db/`

```
db/
├── schema.sql                      # complete DDL reference (mirrors migration 001)
├── seed/
│   ├── 01_reference_data.sql       # REQUIRED in every environment incl. production
│   └── 02_demo_data.sql            # local / CI / staging only; guarded by APP_ENV
└── migrations/                     # symlink note: Alembic versions live in backend/migrations
```

---

## 15.5 Naming conventions

| Layer | Convention | Example |
|-------|-----------|---------|
| Database table | `snake_case`, plural | `loan_applications` |
| Database column | `snake_case` | `days_past_due` |
| Index | `idx_<table>_<cols>` / `uq_<table>_<cols>` | `idx_loans_active_dpd` |
| Constraint | `ck_<table>_<rule>` / `fk_<table>_<ref>` | `ck_routes_distance` |
| Python module | `snake_case` | `route_assessment_service.py` |
| Python class | `PascalCase` | `RouteScoringEngine` |
| Python function | `snake_case`, verb-first | `calculate_emi`, `evaluate_rule` |
| Pydantic schema | `<Entity><Action>` | `RouteCreate`, `RouteRead`, `RouteAssessmentResult` |
| API path | `kebab-case`, plural nouns | `/api/v1/loan-applications` |
| Permission | `resource:action` | `application:approve` |
| React component | `PascalCase` file and export | `ScoreGauge.tsx` |
| React hook | `useCamelCase` | `useAssessRoute` |
| TS type | `PascalCase` | `RouteAssessmentResult` |
| Test | `test_<what>_<condition>_<expectation>` | `test_score_rejects_when_dscr_below_minimum` |
| Branch | `<type>/<ticket>-<slug>` | `feat/EVRCA-142-route-score-result` |
| Commit | Conventional Commits | `feat(scoring): add charging adequacy cap` |
| Engine version | `<engine>@<semver>` | `route-engine@1.1.0` |

---

## 15.6 Makefile targets

```makefile
make up            # docker compose up -d --build
make down          # stop and remove
make logs          # tail all services
make migrate       # alembic upgrade head
make revision m=".."  # autogenerate a migration
make seed          # reference data only
make demo-reset    # reference + demo data, dates realigned to today
make demo-alerts   # force a rule evaluation for the demo date
make types         # regenerate frontend types from the running OpenAPI schema
make test          # backend unit + integration
make test-fe       # frontend unit
make e2e           # Playwright against the compose stack
make lint          # ruff, black --check, mypy, eslint, tsc
make fmt           # ruff --fix, black, prettier
make security      # bandit, semgrep, pip-audit, npm audit, gitleaks
make backup        # pg_dump to ./backups, encrypted
make restore f=..  # restore from a dump
```

---

## 15.7 Environment variables

Full reference in [02-TRD.md](02-TRD.md) §2.17. `.env.example` documents every variable with a
description and no value. The application **fails to start** if a required variable is missing —
`pydantic-settings` validates the whole configuration at boot, so a misconfiguration is a startup
error rather than a 3 a.m. runtime surprise.

---

## 15.8 Git workflow

| Item | Convention |
|------|-----------|
| Branches | `main` (always deployable) ← `feat/*`, `fix/*`, `chore/*`, `docs/*` |
| Protection | No direct pushes to `main`; PR requires CI green + 1 approval + coverage gates |
| Commits | Conventional Commits; the scope is the module (`scoring`, `alerts`, `api`, `ui`) |
| PR template | What / Why / How tested / Screenshots / Migration notes / Breaking changes |
| Releases | Semantic version tags `v1.2.0`; release notes generated from commits |
| Migrations | One per PR maximum; expand-then-contract; never edit a merged migration |
| Engine changes | Must bump `engine_version` and add a changelog entry — enforced by a CI check |
| Documentation | A change to scoring, the API or the schema must update the corresponding `docs/` file in the same PR |
