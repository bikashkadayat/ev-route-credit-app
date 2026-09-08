# 4. System Architecture

**System:** EV-RCA · **Version:** 1.0 · **Audience:** Architects, backend/frontend engineers, DevOps

---

## 4.1 Architectural goals and constraints

| Goal | Constraint it must respect |
|------|---------------------------|
| Deterministic, reproducible credit decisions | The engine must never depend on wall-clock time, network state or mutable globals |
| Risk policy owned by the business | Weights, thresholds and rules are data, versioned, publishable without a deploy |
| Auditable end to end | Every state change carries actor, timestamp, before/after and request id |
| Buildable by 4 engineers in 14 weeks | One deployable backend, one frontend, one database, four containers |
| Runs inside a bank's own datacentre | No mandatory managed cloud service; everything is a container on Ubuntu |
| Extractable later | Module boundaries are hard; `monitoring` and `risk` are the designed extraction seams |
| Degrades safely | Loss of an external provider reduces functionality, never corrupts data or silently scores as healthy |

---

## 4.2 Logical architecture

```mermaid
flowchart TB
    subgraph L1["Presentation Layer"]
        UI["Next.js 15 App Router<br/>Server Components (dashboards, lists)<br/>Client Components (forms, charts, tables)"]
    end

    subgraph L2["Edge Layer"]
        NGX["Nginx<br/>TLS 1.2+, HTTP/2, brotli<br/>security headers, IP rate limit<br/>static asset cache, reverse proxy"]
    end

    subgraph L3["API Layer — FastAPI"]
        MID["Middleware chain<br/>1 request-id · 2 CORS · 3 rate limit<br/>4 auth (JWT) · 5 RBAC dependency<br/>6 audit context · 7 error handler · 8 metrics"]
        RT["Routers /api/v1<br/>auth · users · routes · applicants<br/>applications · scoring · loans<br/>portfolio · alerts · dashboard · admin"]
    end

    subgraph L4["Application / Service Layer"]
        SVC["Use-case services<br/>transaction boundaries, orchestration,<br/>permission scoping, audit emission"]
    end

    subgraph L5["Domain Layer — pure, no I/O"]
        RE1["Route Scoring Engine"]
        RE2["Customer Scoring Engine"]
        RE3["Vehicle Economics Calculator"]
        RE4["Combined Risk Engine"]
        RE5["Loan Structuring & EMI Calculator"]
        RE6["Risk Rule Engine"]
        KO["Knock-out / Decision Matrix"]
    end

    subgraph L6["Persistence Layer"]
        REPO["Repositories (SQLAlchemy 2.0)"]
        PG[("PostgreSQL 16<br/>28 tables · partitioned telemetry<br/>materialised views for dashboards")]
        CACHE[("Redis — optional at MVP<br/>config cache, rate limit, Celery broker")]
        FILES[["Document store<br/>volume (MVP) → object storage (prod)"]]
    end

    subgraph L7["Integration Layer — adapter pattern"]
        A1["CIBProvider<br/>MockCIB ⇄ LiveCIB"]
        A2["TelematicsProvider<br/>MockGPS ⇄ LiveGPS"]
        A3["ChargingStationProvider"]
        A4["NotificationProvider<br/>SMTP / in-app / SMS(future)"]
    end

    subgraph L8["Async Layer"]
        SCH["Scheduler<br/>cron (MVP) → Celery Beat (prod)"]
        WRK["Workers<br/>ingest · DPD · baselines · rules · notify"]
    end

    UI --> NGX --> MID --> RT --> SVC
    SVC --> RE1 & RE2 & RE3 & RE4 & RE5 & RE6 & KO
    SVC --> REPO --> PG
    SVC --> CACHE
    SVC --> FILES
    SVC --> A1 & A2 & A3 & A4
    SCH --> WRK --> SVC
```

**Dependency rule:** arrows point downward only. The domain layer knows nothing about HTTP, the
database or providers. Services depend on abstractions (`CIBProvider`), never on concrete adapters.

---

## 4.3 Physical / deployment architecture

### 4.3.1 MVP — single host, four containers

```mermaid
flowchart TB
    subgraph Internet
        U1["Head-office users"]
        U2["Branch users"]
        U3["Field officers (mobile browser)"]
    end
    subgraph VM["Ubuntu 22.04 LTS · 4 vCPU · 8 GB RAM · 100 GB SSD"]
        subgraph DC["Docker Compose network 'evrca-net'"]
            NG["nginx:alpine<br/>:80 → 443"]
            WEB["web — Next.js<br/>:3000"]
            API["api — FastAPI/uvicorn<br/>:8000 · 2 workers"]
            DB[("db — postgres:16<br/>:5432 · volume pgdata")]
            CRON["cron sidecar<br/>invokes api CLI jobs"]
        end
        VOL[["volumes: pgdata, documents, backups"]]
    end
    U1 & U2 & U3 -->|HTTPS| NG
    NG -->|/| WEB
    NG -->|/api| API
    WEB -->|server-side fetch| API
    API --> DB
    CRON --> API
    DB --- VOL
```

### 4.3.2 Production — horizontally scalable

```mermaid
flowchart TB
    LB["Load balancer / Nginx pair<br/>TLS, WAF rules, rate limit"]
    subgraph AppTier["Application tier (stateless, N replicas)"]
        W1["web-1"]; W2["web-2"]
        A1["api-1"]; A2["api-2"]
    end
    subgraph WorkerTier["Worker tier"]
        CW1["celery-worker-1"]; CW2["celery-worker-2"]; CB["celery-beat (singleton)"]
    end
    subgraph DataTier["Data tier"]
        PGB["PgBouncer (transaction pooling)"]
        PGP[("PostgreSQL primary")]
        PGR[("PostgreSQL streaming replica<br/>dashboards & reports")]
        RDS[("Redis — cache, broker, rate limit")]
        OBJ[["Object storage — documents, backups"]]
    end
    subgraph Obs["Observability"]
        PRM["Prometheus"]; GRF["Grafana"]; LOK["Loki"]; SNT["Sentry"]
    end
    LB --> W1 & W2
    LB --> A1 & A2
    W1 & W2 --> A1
    A1 & A2 --> PGB --> PGP
    A1 & A2 -->|read-only queries| PGR
    A1 & A2 --> RDS
    CB --> RDS --> CW1 & CW2
    CW1 & CW2 --> PGB
    A1 & A2 & CW1 --> OBJ
    A1 & A2 & CW1 -.metrics/logs.-> PRM & LOK
    PRM --> GRF
    PGP -->|streaming replication| PGR
```

**Why the app tier is trivially horizontal:** no in-process session state (sessions in Postgres/
Redis), no local file writes on the request path (documents go to shared storage), scoring is pure.
The only singleton is Celery Beat.

---

## 4.4 Component inventory

| Component | Technology | Responsibility | Scaling | Failure mode |
|-----------|-----------|----------------|---------|--------------|
| `nginx` | Nginx 1.25 | TLS, routing, static cache, edge rate limit, security headers | vertical / pair | total outage — mitigate with a second instance |
| `web` | Next.js 15 | UI rendering, session cookie, server-side data fetch | horizontal | UI down, API unaffected |
| `api` | FastAPI + uvicorn | All business logic and persistence | horizontal | full outage; health checks + restart policy |
| `db` | PostgreSQL 16 | System of record | vertical + replica | total outage; PITR restore |
| `redis` (prod) | Redis 7 | Cache, broker, distributed rate limit | vertical / cluster | cache miss path still works; jobs queue |
| `celery-worker` (prod) | Celery | Batch jobs | horizontal | jobs delayed, alerts stale; `job_last_success` alerting |
| `celery-beat` (prod) | Celery Beat | Schedule | singleton | no jobs scheduled; monitored gauge |
| Document store | volume / S3 | Uploaded files | storage tier | uploads fail; core flows unaffected |

---

## 4.5 Request flow (generic)

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant N as Nginx
    participant W as Next.js server
    participant A as FastAPI
    participant S as Service
    participant D as PostgreSQL
    participant AU as Audit

    U->>N: HTTPS GET /applications/42
    N->>W: proxy (adds X-Forwarded-*)
    W->>W: read httpOnly session cookie, get access token
    W->>A: GET /api/v1/applications/42 (Bearer, X-Request-ID)
    A->>A: MW1 assign/propagate request_id
    A->>A: MW3 rate limit (per user+IP)
    A->>A: MW4 verify JWT signature, exp, aud, iss
    A->>A: Depends(require_permission("application:read"))
    A->>S: get_application(id=42, actor=user)
    S->>D: SELECT ... JOIN route_assessments, credit_scores
    D-->>S: rows
    S->>S: row-scope check (branch / assignment)
    S-->>A: ApplicationDetailDTO
    A->>AU: write READ audit entry (sensitive entities only)
    A-->>W: 200 JSON + X-Request-ID
    W-->>U: rendered HTML / hydrated JSON
    Note over A,D: Any exception → global handler → RFC7807 envelope + logged with request_id
```

---

## 4.6 Authentication flow

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant W as Next.js server
    participant A as FastAPI /auth
    participant D as PostgreSQL

    U->>W: POST /login {email, password}
    W->>A: POST /api/v1/auth/login
    A->>D: SELECT user WHERE email, is_active
    alt user missing or inactive
        A-->>W: 401 INVALID_CREDENTIALS (constant-time, generic message)
    else locked
        A-->>W: 423 ACCOUNT_LOCKED (retry_after)
    else valid
        A->>A: argon2.verify(password, hash)
        A->>D: reset failed_attempts; INSERT user_sessions (refresh hash, family_id, UA, IP)
        A->>D: INSERT audit_logs (LOGIN_SUCCESS)
        A-->>W: 200 {access_token(15m), refresh_token, user{role, permissions[]}}
    end
    W->>U: Set-Cookie refresh (httpOnly, Secure, SameSite=Lax) + session payload

    Note over U,A: ── silent refresh ──
    U->>W: any request after access token expiry
    W->>A: POST /api/v1/auth/refresh {refresh_token}
    A->>D: lookup token hash
    alt token already rotated (reuse detected)
        A->>D: revoke ENTIRE session family; audit SECURITY_REFRESH_REUSE
        A-->>W: 401 → force re-login
    else valid
        A->>D: rotate (mark old used, insert new in same family)
        A-->>W: 200 new access + new refresh
    end
```

Logout revokes the presented refresh token and its family; `POST /auth/logout-all` revokes every
session for the user.

---

## 4.7 Route scoring flow

```mermaid
sequenceDiagram
    autonumber
    participant CO as Credit Officer
    participant A as API /routes/{id}/assess
    participant S as RouteAssessmentService
    participant C as ConfigService (cached)
    participant E as RouteScoringEngine (pure)
    participant D as PostgreSQL

    CO->>A: POST /routes/{id}/assess (Idempotency-Key)
    A->>A: require_permission("route:assess")
    A->>S: assess(route_id, actor)
    S->>D: SELECT route + charging stations
    S->>S: validate completeness → 422 listing missing fields
    S->>C: get_active_config("ROUTE")
    C->>D: SELECT config + components (cache miss only)
    C-->>S: ScoringConfig(version=7, components[5], thresholds)
    S->>E: score(RouteScoringInput, config)
    Note right of E: pure: normalise each input → 0-100,<br/>apply weights, sum, grade,<br/>build factor + reason lists
    E-->>S: ScoreResult(84.20, "A", components[5], factors, reasons)
    S->>D: BEGIN
    S->>D: INSERT route_assessments (score, grade, config_version_id, engine_version, input_snapshot)
    S->>D: INSERT route_score_components × 5
    S->>D: UPDATE routes SET latest_assessment_id, latest_score, latest_grade
    S->>D: INSERT audit_logs (ROUTE_ASSESSED, before/after)
    S->>D: COMMIT
    S-->>A: RouteAssessmentDTO
    A-->>CO: 201 + full breakdown for the result screen
```

---

## 4.8 Loan application & combined assessment flow

```mermaid
flowchart TD
    S1["DRAFT — officer captures applicant, financials, vehicle, route"] --> S2{"All required data present?"}
    S2 -->|no| S1
    S2 -->|yes| S3["Pull credit bureau report (cached if < 90 days)"]
    S3 --> S4["Evaluate KNOCK-OUT rules"]
    S4 -->|any hit| R1["Decision REJECT<br/>reason codes KO_*<br/>no scoring performed"]
    S4 -->|clear| S5["Route score — reuse latest non-stale assessment, else assess now"]
    S5 --> S6["Customer score — 6 weighted components"]
    S6 --> S7["Vehicle economics — NPR/km, net contribution, payback"]
    S7 --> S8["Combined risk score = 0.40·route + 0.40·customer + 0.20·vehicle"]
    S8 --> S9["Loan structuring — max LTV by grade, EMI capacity, tenure cap, rate"]
    S9 --> S10["Compute EMI and DSCR"]
    S10 --> S11{"Decision matrix"}
    S11 -->|"score ≥ 75 · grade A/B · route A/B · DSCR ≥ 1.30 · FOIR ≤ 0.50"| D1["APPROVE"]
    S11 -->|"score < 50 · grade E · DSCR < 1.00"| D2["REJECT"]
    S11 -->|otherwise| D3["MANUAL REVIEW → queue for Risk Manager"]
    D1 & D2 & D3 --> P["Persist underwriting_decisions<br/>+ reasons + config version + input snapshot + audit"]
    P --> O{"Authorised override?"}
    O -->|yes, with justification ≥ 20 chars| P2["Persist override, link original, audit OVERRIDE"]
    O -->|no| F["Final decision"]
    P2 --> F
    F --> L{"APPROVED?"}
    L -->|yes| BK["Book loan → generate amortisation schedule → activate monitoring"]
    L -->|no| CL["Close application with reasons; applicant letter data available"]
```

---

## 4.9 Monitoring flow (post-disbursement)

```mermaid
sequenceDiagram
    autonumber
    participant SCH as Scheduler
    participant ING as TelemetryService
    participant TP as TelematicsProvider (mock/live)
    participant D as PostgreSQL
    participant BL as BaselineService
    participant DPD as LoanService

    SCH->>ING: ingest_telemetry(as_of=yesterday)
    ING->>TP: fetch_daily(vehicle_ids[], date)
    TP-->>ING: [{vehicle_id, km, trips, avg_speed, active_hours, idle_min, deviation_pct, soc, soh, sessions}]
    ING->>ING: validate (km ≥ 0, ≤ 800/day; speed plausible; timestamp not future)
    ING->>D: bulk UPSERT vehicle_telemetry (partitioned by month)
    ING->>D: bulk UPSERT battery_metrics, charging_sessions
    ING->>D: flag vehicles with no data → MONITORING_DEGRADED
    SCH->>DPD: recompute_dpd_and_outstanding(as_of)
    DPD->>D: UPDATE repayment_schedules SET days_past_due; UPDATE loans SET outstanding, overdue, dpd, classification
    SCH->>BL: rebuild_usage_baselines(as_of)
    BL->>D: window functions → 7d/30d/90d avg km, trips, sessions, revenue per loan
    BL->>D: UPSERT loan_monitoring_snapshots
```

---

## 4.10 Alert generation flow

```mermaid
sequenceDiagram
    autonumber
    participant SCH as Scheduler
    participant RE as RuleEngine
    participant D as PostgreSQL
    participant AS as AlertService
    participant N as NotificationService

    SCH->>RE: evaluate_risk_rules(as_of)
    RE->>D: SELECT active rules + conditions (ordered by severity DESC)
    RE->>D: build MetricSnapshot per active loan (set-based SQL, one pass)
    loop each active loan
        loop each active rule
            RE->>RE: evaluate conditions (ALL/ANY) against snapshot
            alt condition true
                RE->>AS: raise(loan, rule, metric_value, threshold)
                AS->>D: SELECT open alert for (loan, rule)
                alt already open
                    AS->>D: UPDATE last_evaluated_at, current_value, occurrence_count
                else new
                    AS->>D: INSERT risk_alerts (OPEN, severity, trigger snapshot, SLA due)
                    AS->>D: supersede lower-severity alerts on the same signal family
                    AS->>N: enqueue notification (in-app + email for RED)
                    AS->>D: INSERT audit_logs (ALERT_RAISED)
                end
            else condition false and an alert is open
                AS->>D: auto-resolve with AUTO_CONDITION_CLEARED + audit
            end
        end
    end
    SCH->>AS: escalate_sla_breached_alerts()
    AS->>D: UPDATE breached alerts → ESCALATED, reassign to supervisor
    AS->>N: notify supervisors
```

---

## 4.11 External integrations

All external systems sit behind an abstract provider. Switching from mock to live is one environment
variable; no service or engine code changes.

```python
# backend/app/integrations/cib/base.py
class CIBProvider(ABC):
    @abstractmethod
    async def fetch_report(self, *, id_type: str, id_number: str,
                           full_name: str) -> CIBReport: ...
```

| Integration | MVP | Production | Contract | Failure behaviour |
|-------------|-----|-----------|----------|-------------------|
| **Credit bureau (CIB)** | `MockCIBProvider` — deterministic pseudo-random report seeded by ID number so demos are stable | REST over TLS with mTLS or API key; per-enquiry billing | `fetch_report(id_type, id_number, full_name) -> CIBReport{score, grade, history_months, defaults[], overdue, max_dpd, active_loans, blacklisted, enquiries_6m, raw}` | 3 retries with backoff → circuit breaker → officer may enter bureau data manually with a `MANUAL_BUREAU_ENTRY` flag; the file cannot auto-APPROVE without bureau data |
| **Telematics / GPS** | `MockTelematicsProvider` — generates plausible daily series per vehicle profile, with injectable "distress" scenarios for the demo | OEM cloud API or aftermarket OBD platform; daily aggregate pull | `fetch_daily(vehicle_ids, date) -> list[TelemetryRecord]` | missing data → `MONITORING_DEGRADED` + stale-feed alert; usage rules skipped, never scored as healthy |
| **Charging stations** | Seeded table maintained by Ops | Optional charging-network API; nightly refresh with `verified_at` | `list_stations(bbox\|corridor) -> list[Station]` | falls back to the stored registry; staleness surfaced on the route screen |
| **Notifications** | SMTP (in-app always) | SMTP + SMS gateway | `send(channel, to, template, context)` | queued with retry; failure is logged and visible in the notification centre, never blocks the business transaction |

**Mock CIB response example** (`api-examples/cib-mock-response.json`):

```json
{
  "provider": "MOCK_CIB",
  "enquiry_id": "CIB-MOCK-2026-0000431",
  "enquiry_date": "2026-09-08",
  "subject": {"id_type": "CITIZENSHIP", "id_number": "27-01-70-04521", "full_name": "Ram Bahadur Tamang"},
  "score": 742,
  "score_range": {"min": 300, "max": 900},
  "grade": "B",
  "credit_history_months": 54,
  "active_loan_count": 2,
  "total_outstanding_npr": 385000.00,
  "total_monthly_emi_npr": 12400.00,
  "previous_default_count": 0,
  "current_overdue_npr": 0.00,
  "max_dpd_last_24m": 12,
  "blacklisted": false,
  "enquiries_last_6m": 1,
  "repayment_history": [
    {"month": "2026-08", "status": "ON_TIME"},
    {"month": "2026-07", "status": "ON_TIME"},
    {"month": "2026-06", "status": "LATE_1_30"}
  ]
}
```

---

## 4.12 Data flow and system boundaries

```mermaid
flowchart LR
    subgraph Input["Data entering the system"]
        I1["Officer-entered route data"]
        I2["Officer-entered applicant & financials"]
        I3["Uploaded documents"]
        I4["Bureau report (API)"]
        I5["Telemetry (API, daily)"]
        I6["Repayment postings (manual/CBS)"]
        I7["Admin configuration"]
    end
    subgraph Core["System of record — PostgreSQL"]
        C1["Reference & config"]; C2["Entities: routes, applicants, vehicles, applications"]
        C3["Immutable events: assessments, scores, decisions, audit"]
        C4["Operational: loans, schedules, repayments"]
        C5["Time-series: telemetry, battery, charging"]
        C6["Derived: baselines, alerts, materialised views"]
    end
    subgraph Output["Data leaving the system"]
        O1["Screens & dashboards"]; O2["CSV / PDF exports"]
        O3["Email / in-app notifications"]; O4["Audit extracts for inspection"]
    end
    I1 & I2 & I3 & I4 & I7 --> C1 & C2
    C2 --> C3 --> C4
    I5 --> C5 --> C6
    I6 --> C4 --> C6
    C6 --> O1 & O3
    C3 --> O4
    C2 & C4 --> O2
```

**Boundary rules:** the platform never writes to the core banking system in MVP; disbursement and
GL remain external. Documents never leave the institution's storage. No PII is sent to any external
service except the bureau enquiry, which is contractual and audit-logged per enquiry.

---

## 4.13 Caching strategy

| Data | Where | TTL / invalidation | Rationale |
|------|-------|--------------------|-----------|
| Active scoring configuration | in-process dict (MVP) / Redis (prod) | keyed by `config_version_id`; publishing a new version invalidates | read on every scoring call, changes rarely |
| Permission set per role | in-process | 5 min or on role change | read on every request |
| Dashboard aggregates | materialised views in Postgres | refreshed nightly + on demand after a booking | expensive multi-join aggregates |
| Bureau report | `credit_bureau_reports` table | configurable validity, default 90 days | per-enquiry cost |
| Vehicle model master | in-process | 1 h | small, static |
| Session/token blacklist | Postgres (MVP) / Redis (prod) | until expiry | revocation correctness over speed |

---

## 4.14 Failure modes and degradation matrix

| Failure | Detection | System behaviour | User-visible result |
|---------|-----------|------------------|---------------------|
| PostgreSQL down | `/health/ready` fails | API returns 503; no writes | Maintenance banner; nothing lost |
| Bureau provider down | circuit breaker opens | Application stays in DRAFT; officer may enter bureau data manually with a flag | "Bureau unavailable — enter manually or retry" |
| Telematics feed silent | ingest job records zero rows for a vehicle | Loan flagged `MONITORING_DEGRADED`; stale-feed alert raised at day 2 and day 5 | Amber banner on the loan monitoring screen |
| Nightly rule job fails | `job_last_success_timestamp` stale > 26 h | Alerting fires; job re-runnable for the same `as_of_date` idempotently | Alerts may be one day late; banner on the alerts screen |
| Redis down (prod) | connection error | Falls back to in-process cache and DB-backed rate limiting; Celery pauses | Slight latency increase; jobs delayed |
| Disk full | Prometheus disk alert at 85% | Uploads rejected with 507; DB protected by reserved space | Upload errors only |
| Scoring config missing/unpublished | readiness check | Scoring endpoints return 409 `CONFIG_NOT_PUBLISHED` | Clear message directing an admin to publish |

---

## 4.15 Security architecture summary

Detail in [09-SECURITY-ARCHITECTURE.md](09-SECURITY-ARCHITECTURE.md).

```mermaid
flowchart LR
    subgraph Perimeter
        TLS["TLS 1.2+ · HSTS · modern ciphers"]
        WAF["Nginx: rate limit, request size cap,<br/>method allow-list, security headers"]
    end
    subgraph AppSec
        AUTH["JWT verify · session validity"]
        RBAC["Permission dependency per endpoint"]
        SCOPE["Row-level scoping in services"]
        VAL["Pydantic validation on every input"]
        ORM["Parameterised SQL only"]
    end
    subgraph DataSec
        ENC["pgcrypto field encryption:<br/>national ID, PAN, account no."]
        REST["Encrypted volumes / disks at rest"]
        MASK["Masked display + reveal permission"]
        AUD["Append-only audit_logs"]
    end
    TLS --> WAF --> AUTH --> RBAC --> SCOPE --> VAL --> ORM --> ENC --> REST --> MASK --> AUD
```

---

## 4.16 Environments and promotion

| Environment | Host | Data | Access | Purpose |
|-------------|------|------|--------|---------|
| Local | developer machine | seeded demo pack | developer | development |
| CI | ephemeral runners | throwaway | pipeline | automated verification |
| Staging | 2 vCPU / 4 GB VM | synthetic/anonymised | internal, IP-restricted | UAT, demo, training |
| Production | 4 vCPU / 8 GB (MVP) | live | internal network / VPN | live operations |

Promotion: tag → CI green → deploy to staging → smoke tests → business UAT sign-off → manual
approval → production deploy with migration step and post-deploy health verification.

---

## 4.17 Key architectural decisions (summary)

| # | Decision | Alternative rejected | Reason |
|---|----------|---------------------|--------|
| 1 | Modular monolith | Microservices | 4-person team; boundaries still moving; extraction seams preserved |
| 2 | Configuration in DB, versioned | Constants / YAML in repo | Risk policy must change without a deploy and remain reproducible |
| 3 | Pure scoring engines | Scoring inside services | Determinism, testability, dry-run capability |
| 4 | PostgreSQL only | Postgres + Mongo + TSDB | Operability for one small team; JSONB and partitioning suffice |
| 5 | Adapter pattern for all externals | Direct client calls | Demo without contracts; testable; swappable |
| 6 | Cron-driven jobs at MVP, Celery later | Celery from day 1 | Fewer moving parts for the MVP; identical CLI entry points |
| 7 | Server-rendered dashboards | Full client-side SPA | Faster first paint on heavy read pages; simpler auth |
| 8 | Append-only event tables | Update-in-place | Historical decisions must be reproducible and inspectable |
| 9 | Monthly partitions on telemetry from day 1 | Add partitioning later | Repartitioning a large live table is painful |
| 10 | Idempotency keys on money/job endpoints | Best-effort | Prevents duplicate loans and duplicate repayment postings |
