# 9. Security Architecture

**Audience:** Security officer, DevOps, engineers, compliance
**Classification of the data handled:** personal identifiers, financial position, credit bureau
data, vehicle location — all of it regulated, all of it attractive to an attacker.

---

## 9.1 Threat model

| # | Threat | Actor | Impact | Primary controls |
|---|--------|-------|--------|------------------|
| T1 | Credential theft / brute force | External | Full account takeover | Argon2id, lockout, rate limiting, MFA (Phase 2), session revocation |
| T2 | Privilege escalation (a Credit Officer approving their own file) | Internal | Fraudulent lending | Server-side RBAC on every endpoint, row scoping, four-eyes above threshold, audit |
| T3 | Bulk PII exfiltration | Internal / compromised account | Regulatory breach, reputational loss | Export rate limits, export audit + watermarking, masked identifiers by default, field-level encryption |
| T4 | SQL injection | External | Data theft or destruction | ORM parameterisation only, no string-built SQL, input validation |
| T5 | XSS → session theft | External | Account takeover | React escaping, strict CSP, httpOnly cookies, sanitised rich text |
| T6 | CSRF on cookie-authenticated actions | External | Unauthorised writes | SameSite=Lax, double-submit token, no state-changing GETs |
| T7 | Tampering with a decision after the fact | Internal | Audit failure | Append-only tables, DB triggers, no DELETE grant, immutable audit |
| T8 | Scoring configuration abuse (silently loosening credit) | Internal | Portfolio loss | Publish permission separated, versioned configs, diff-on-publish, audit, override-rate KPI |
| T9 | Token replay after logout | External | Session hijack | Short access tokens, server-side refresh store, rotation with reuse detection |
| T10 | Supply-chain compromise | External | Arbitrary code execution | Pinned dependencies, lockfiles, `pip-audit`/`npm audit`, Trivy image scanning, no `latest` tags |
| T11 | Backup theft | External / insider | Full data loss | Encrypted backups, separate credentials, off-site with restricted access |
| T12 | Denial of service | External | Availability loss | Nginx rate limiting, request size caps, connection limits, query timeouts |
| T13 | Insecure direct object reference | Authenticated | Cross-customer data access | UUIDs in the API, ownership/scope checks in services, 404 (not 403) for out-of-scope ids |
| T14 | Document upload as an attack vector | Authenticated | RCE, stored XSS | Magic-byte validation, MIME allow-list, size cap, stored outside the web root, served with `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff` |

---

## 9.2 Authentication

### 9.2.1 Password handling

| Control | Setting |
|---------|---------|
| Algorithm | **Argon2id** — `time_cost=3`, `memory_cost=65536` (64 MB), `parallelism=4`, 16-byte salt, 32-byte hash |
| Minimum length | 12 characters |
| Complexity | Upper + lower + digit + symbol |
| Reuse | Last 5 hashes retained per user and rejected |
| Rotation | 90 days for privileged roles; not forced for others (forced rotation encourages weak increments) |
| Breach check | Reject passwords appearing in a local common-password list (top 10k) |
| Storage | Only the hash. The plaintext never leaves the request scope and is never logged. |
| Comparison | Constant-time; identical response body and timing for unknown-email and wrong-password |
| First login | `must_change_password = true` for admin-created accounts |

### 9.2.2 Account lockout

5 failed attempts within 15 minutes → locked 15 minutes. Counter resets on success. Lockout is
recorded in `audit_logs` and notifies the user by email. A separate per-IP limit (5/min) blocks
distributed guessing across accounts.

### 9.2.3 Token design

| | Access token | Refresh token |
|---|---|---|
| Type | JWT (HS256 in MVP, **RS256 in production**) | Opaque, 256-bit CSPRNG |
| Lifetime | 15 minutes | 7 days, rotating |
| Storage (client) | Memory / `Authorization` header | httpOnly + Secure + SameSite=Lax cookie |
| Storage (server) | Not stored (stateless) | SHA-256 hash in `user_sessions` |
| Claims | `sub` (uuid), `email`, `role`, `permissions[]`, `jti`, `iat`, `exp`, `iss`, `aud` | — |
| Revocation | Short lifetime + a `jti` denylist for immediate revocation | Row revocation, family revocation |

**Rotation with reuse detection.** Every refresh issues a new refresh token and marks the old one
`rotated_at`. Presenting an already-rotated token means it was stolen: the entire `family_id` is
revoked, a `SECURITY_REFRESH_REUSE` audit event is written, and the user is notified. This turns a
stolen refresh token from a persistent backdoor into a single-use event that trips an alarm.

**Why not store the access token in `localStorage`:** any XSS reads it. Memory-only for the access
token plus an httpOnly cookie for the refresh token means an XSS payload cannot exfiltrate long-lived
credentials.

### 9.2.4 Session management

| Control | Behaviour |
|---------|-----------|
| Idle timeout | 30 minutes of no API activity → refresh refused, re-login required |
| Absolute timeout | 12 hours regardless of activity |
| Concurrent sessions | Allowed; all listed in the user profile with device, IP and last-seen; individually revocable |
| Logout | Revokes the presented token and its family |
| Logout everywhere | Revokes all families for the user |
| Password change | Revokes every other session |
| Deactivation | Revokes every session immediately |
| Privilege change | Revokes every session (permissions are in the token, so they must be re-minted) |

### 9.2.5 Multi-factor authentication — [S] Phase 2

TOTP (RFC 6238), mandatory for Super Admin and Risk Manager, optional for others. Ten single-use
recovery codes issued at enrolment, hashed at rest. Enrolment and reset are audit-logged.

---

## 9.3 Authorisation

### 9.3.1 Three enforcement layers

1. **Endpoint permission.** Every non-public route declares
   `Depends(require_permission("application:approve"))`. The permission is visible in the router
   file, so an auditor can read authorisation from the code.
2. **Row scope.** Services apply scope filters: a Field Officer sees only alerts assigned to them; a
   branch user sees only their branch's applications; an officer cannot decide an application they
   created (segregation of duties, enforced in `UnderwritingService`).
3. **Field visibility.** Sensitive fields (national ID, PAN, full bureau payload) are masked in
   responses unless the caller holds `applicant:read_sensitive`; unmasking is audit-logged.

### 9.3.2 Principles

- **Deny by default.** A route without a declared permission fails a CI check that scans the router
  tree; it cannot ship.
- **Least privilege.** Roles hold the minimum needed. Viewer holds only `*:read`.
- **No client-side authority.** Hidden UI is a convenience. Every check is repeated server-side.
- **404 over 403 for out-of-scope objects.** Returning 403 for an id that exists but is out of scope
  confirms its existence; return 404.
- **Segregation of duties.** Create ≠ approve. Approve ≠ disburse. Configure ≠ audit.

### 9.3.3 Sensitive operations requiring elevated control

| Operation | Control |
|-----------|---------|
| Override a system recommendation | `application:override` + justification ≥ 20 chars + audit |
| Publish a scoring configuration | `risk:config:publish` + change note + diff shown + audit |
| Approve above the delegation limit | Second approver (four-eyes) — [S] Phase 2 |
| Unmask a national ID | `applicant:read_sensitive` + audit per view |
| Export any dataset | `report:read` + audit with filter set and row count + watermark |
| Deactivate a user | `user:delete` + confirmation + session revocation + audit |
| Grant a Class C route waiver | `risk:waiver` + justification + audit |

---

## 9.4 Input validation and injection defence

| Vector | Control |
|--------|---------|
| **SQL injection** | SQLAlchemy parameterised queries exclusively. Raw SQL is permitted **only** via `text()` with bound parameters, and a CI grep rejects f-string/`%`-formatted SQL. Table/column names are never taken from user input; sort and filter fields are validated against a per-endpoint allow-list. |
| **Schema validation** | Pydantic v2 on every request body, query and path parameter — types, ranges, lengths, enums, regexes, cross-field validators. Unknown fields are rejected (`extra="forbid"`) so a typo cannot silently pass. |
| **Business validation** | Service-layer invariants (down payment < price, LTV within cap, state transitions) that no client can bypass. |
| **Mass assignment** | Separate `Create` / `Update` / `Read` schemas. Server-controlled fields (`id`, `status`, `created_by`, computed scores) are never accepted from the client. |
| **NoSQL / command injection** | No shell execution on user input; no dynamic `eval`; JSONB values are parameterised. |
| **Path traversal** | Uploaded files are stored under a UUID-derived key; the original filename is sanitised, stored as metadata only, and never used for the filesystem path. Download resolves by UUID through the database. |
| **XXE / deserialisation** | JSON only; no XML parsing; no `pickle`; no YAML `load` on user input. |
| **ReDoS** | All regexes are anchored, bounded, and reviewed; no catastrophic backtracking patterns. |
| **Integer/decimal overflow** | Explicit `NUMERIC` bounds and Pydantic `le`/`ge` on every monetary field. |

---

## 9.5 XSS, CSRF and browser hardening

**XSS**
- React escapes by default; `dangerouslySetInnerHTML` is banned by an ESLint rule with no exceptions
  in this codebase.
- Any user-supplied rich text (resolution notes, justifications) is stored raw and rendered as plain
  text, never as HTML.
- Strict CSP served by Nginx:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-{random}';
  style-src 'self' 'nonce-{random}';
  img-src 'self' data: blob:;
  font-src 'self';
  connect-src 'self';
  frame-ancestors 'none';
  form-action 'self';
  base-uri 'self';
  object-src 'none';
  upgrade-insecure-requests
```

**Security headers (all responses)**

| Header | Value |
|--------|-------|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), camera=(), microphone=(), payment=()` |
| `Cache-Control` (authenticated pages/API) | `no-store, no-cache, must-revalidate, private` |

**CSRF**
The API is primarily Bearer-authenticated, which is not CSRF-susceptible. For the cookie-authenticated
Next.js server actions: `SameSite=Lax` on the session cookie, a double-submit CSRF token
(`X-CSRF-Token` header matched against a non-httpOnly cookie) on all state-changing requests, an
`Origin`/`Referer` check, and an absolute rule that **no GET endpoint changes state**.

**CORS** — explicit origin allow-list from `CORS_ORIGINS`; credentials allowed; no wildcard in any
non-local environment; preflight cached 10 minutes.

---

## 9.6 Rate limiting and abuse control

| Layer | Control |
|-------|---------|
| Nginx | 20 req/s per IP burst 40 globally; 5 req/min on `/api/v1/auth/login`; `client_max_body_size 10m`; connection limit 20/IP; slow-request timeouts |
| Application | Per-user and per-endpoint-group limits (§6.1.6) using Redis in production, an in-process token bucket in MVP |
| Account | Lockout after 5 failed logins; password-reset limited to 3/hour/email |
| Export | Maximum 5 exports per user per hour; rows capped at 50,000 per export |
| Bureau enquiries | Per-user daily cap (default 50) to prevent both cost abuse and bulk profiling |
| Response | `429` with `Retry-After`; every trip is audit-logged |

---

## 9.7 Data protection

### 9.7.1 Classification

| Class | Examples | Handling |
|-------|----------|----------|
| **Restricted** | National ID, PAN, bureau raw payload, password hashes, tokens | Encrypted at field level, masked by default, never logged, access audited |
| **Confidential** | Income, expenses, obligations, scores, decisions, telemetry | RBAC + row scope, encrypted in transit and at rest, access audited on export |
| **Internal** | Routes, vehicle models, charging stations, configuration | RBAC |
| **Public** | Nothing in this system | — |

### 9.7.2 Encryption in transit

TLS 1.2 minimum (TLS 1.3 preferred). Modern cipher suites only; no RC4/3DES/CBC-SHA1. HSTS with
preload. Internal service-to-service traffic stays on a private Docker network; database connections
use `sslmode=require` when the database is on a separate host. Certificates from Let's Encrypt with
automated renewal, or the institution's PKI; expiry monitored with a 14-day alert.

### 9.7.3 Encryption at rest

| Layer | Mechanism |
|-------|-----------|
| Disk | LUKS full-disk encryption on the database volume (or the cloud provider's encrypted volumes) |
| Field level | `pgcrypto` `pgp_sym_encrypt` on `applicants.id_number_enc` and `pan_number_enc`; the key comes from the environment, never from the database |
| Duplicate detection without decryption | `id_number_hash` = SHA-256(normalised id + application-wide pepper) with a unique index |
| Backups | Encrypted with a distinct key before leaving the host |
| Documents | Stored on an encrypted volume; served only through an authenticated, audited endpoint |
| Key management | MVP: environment variables from Docker secrets, file mode 0400, rotated annually. Production: a KMS or HashiCorp Vault; envelope encryption; documented rotation with re-encryption migration. |

### 9.7.4 Masking and minimisation

- National ID displays as `27-01-70-****`; phone as `+977-98****4567`; unmasking requires
  `applicant:read_sensitive` and writes an audit entry naming the field and the record.
- API responses never include `password_hash`, `refresh_token_hash`, encrypted blobs or the raw
  bureau payload unless explicitly requested by a permitted role.
- Logs are redacted by key name (`password*`, `*token*`, `*secret*`, `id_number*`, `pan*`,
  `authorization`) before serialisation; the redactor is unit-tested.
- Non-production environments use **synthetic data only**. If a production restore is ever required
  for debugging, an anonymisation script runs as part of the restore, replacing names, IDs, phones
  and emails.
- Retention: applications and decisions 7 years; audit 7 years; raw telemetry 24 months then rolled
  up; sessions 30 days; notifications 12 months. Purges are scheduled, dry-runnable and audited.

---

## 9.8 Audit logging

Covered operationally in [05-DATABASE-DESIGN.md](05-DATABASE-DESIGN.md) §5.7. Security-relevant
properties:

- Written **in the same transaction** as the change — an audit entry cannot be missing for a
  committed change, and cannot exist for a rolled-back one.
- **Append-only**, enforced by a `BEFORE UPDATE OR DELETE` trigger *and* by withholding the `DELETE`
  grant from the application database role. Even a fully compromised application account cannot
  erase its tracks.
- Captures actor, role, request id, IP, user agent, entity, before/after JSONB, changed fields and
  outcome.
- Security events specifically logged: `LOGIN_SUCCESS`, `LOGIN_FAILURE`, `ACCOUNT_LOCKED`,
  `PASSWORD_CHANGED`, `PASSWORD_RESET_REQUESTED`, `SECURITY_REFRESH_REUSE`, `PERMISSION_DENIED`,
  `SENSITIVE_FIELD_VIEWED`, `DATA_EXPORTED`, `CONFIG_PUBLISHED`, `DECISION_OVERRIDE`,
  `USER_DEACTIVATED`, `RATE_LIMIT_EXCEEDED`.
- **Alerting on the audit stream:** ≥ 10 `PERMISSION_DENIED` from one user in 5 minutes; any
  `SECURITY_REFRESH_REUSE`; an export over 10,000 rows; a configuration publish outside business
  hours; a first login from a new country/IP range.
- Retained 7 years, exportable for inspection, and shipped to Loki (production) so a database
  compromise does not also destroy the log copy.

---

## 9.9 Secrets management

| Rule | Implementation |
|------|----------------|
| No secrets in git | `.gitignore` covers `.env*`; `gitleaks` runs in CI and fails the build on a hit |
| No secrets in images | Injected at runtime as environment variables from Docker secrets or the host secret store |
| Documented, not populated | `.env.example` lists every variable with a description and no value |
| Least privilege | The application database role has no `DELETE`, no DDL and no superuser rights; migrations run as a separate owner role |
| Rotation | JWT signing key annually or on suspicion (dual-key verification window during rotation); database password annually; API keys per provider policy |
| Production access | Break-glass only, requires approval, is time-boxed and logged |

---

## 9.10 Dependency and supply-chain security

| Control | Tool / policy |
|---------|---------------|
| Pinned versions | `requirements.txt` with hashes / `poetry.lock`; `package-lock.json` committed |
| Vulnerability scanning | `pip-audit` and `npm audit --audit-level=high` in CI; build fails on high/critical |
| Static analysis | `bandit` (Python), `eslint-plugin-security` (TS), `semgrep` rules for the project's own anti-patterns |
| Container scanning | Trivy on every built image; fails on high/critical with no available fix exempted only by an approved, expiring waiver |
| Base images | Official slim images pinned by digest; rebuilt weekly to absorb OS patches |
| No `latest` tags | Anywhere, in any environment |
| SBOM | Generated per release (CycloneDX) and archived |
| Update cadence | Patch weekly (automated PR), minor monthly, major planned |

---

## 9.11 Infrastructure hardening

| Area | Control |
|------|---------|
| Host | Ubuntu 22.04 LTS, unattended security upgrades, UFW default-deny inbound, only 22/80/443 open, SSH key-only with root login disabled, fail2ban on SSH |
| Containers | Non-root user in every image, read-only root filesystem where possible, `no-new-privileges`, dropped capabilities, resource limits, health checks, restart policies |
| Network | Internal Docker network for service-to-service; **PostgreSQL is never exposed to the host or internet**; egress restricted to the bureau/telematics endpoints |
| Database | `scram-sha-256` auth, `pg_hba.conf` restricted to the application network, statement timeout 30s, `log_min_duration_statement=1000`, no superuser for the app |
| Nginx | TLS config from the Mozilla intermediate profile, server tokens off, method allow-list, body size cap, timeouts, security headers |
| Admin access | Application administration through the UI only; no direct database editing in production without a change record |

---

## 9.12 Backup and disaster recovery

| Item | MVP | Production |
|------|-----|-----------|
| Database backup | Nightly `pg_dump -Fc`, encrypted, 30 daily + 12 monthly | Base backup + continuous WAL archiving (PITR) |
| Off-site | Weekly encrypted copy to separate storage with separate credentials | Continuous, separate account/region |
| Documents | Nightly volume snapshot | Versioned object storage with lifecycle rules |
| Restore testing | Once before go-live, documented and signed | Quarterly, timed, signed off |
| **RPO / RTO** | 24 h / 8 h | 15 min / 2 h |
| Ransomware resilience | Backups immutable/write-once where supported; backup credentials separate from application credentials |

**Restore runbook (summary):** provision a clean host → start `db` → restore the latest encrypted
dump → `alembic upgrade head` → start `api`, `web`, `nginx` → run the readiness checklist (login,
run a route assessment, open the dashboard, verify the latest audit entry, verify one loan's
schedule totals) → repoint DNS → record the incident. Full runbook lives at
`docs/runbooks/disaster-recovery.md`, to be completed in Phase 7.

---

## 9.13 Incident response

| Phase | Actions |
|-------|---------|
| **Detect** | Alerting on the audit stream, Prometheus alerts, Sentry errors, user reports |
| **Triage** | Severity 1 (data breach / total outage) · 2 (privilege escalation, partial outage) · 3 (single-user impact). S1/S2 page immediately. |
| **Contain** | Revoke sessions (`logout-all` for affected users), disable accounts, rotate the JWT key, block IPs at Nginx, take the service offline if data integrity is at risk |
| **Eradicate** | Patch, redeploy from a known-good image, restore from backup if data was tampered with |
| **Recover** | Verify integrity via the audit log, restore service, monitor closely for 72 hours |
| **Review** | Blameless post-mortem within 5 working days; corrective actions tracked to closure |
| **Notify** | Regulator and affected customers per the institution's data-breach policy; the audit log provides the scope of access |

---

## 9.14 Compliance mapping

| Requirement | How the platform satisfies it |
|-------------|-------------------------------|
| Credit-decision inspectability | Every decision stores its inputs, configuration version, engine version and reasons; any historical score is reproducible |
| Segregation of duties | Role separation; creator ≠ approver enforced in the service layer |
| Audit trail | Append-only, 7-year retention, exportable, database-enforced immutability |
| KYC record retention | Documents retained with checksums; access audited |
| Bureau enquiry governance | One record per enquiry with the requesting user, cached to avoid unnecessary re-enquiry |
| Data privacy | Encryption, masking, minimisation, retention limits, purpose limitation (no external sharing beyond the contracted bureau) |
| Change control on credit policy | Draft → review → publish with a mandatory change note, diff and audit entry |
| Business continuity | Documented and tested backup/restore with stated RPO/RTO |

---

## 9.15 Security testing

| Test | Frequency | Owner |
|------|-----------|-------|
| SAST (`bandit`, `semgrep`, `eslint-plugin-security`) | Every commit | CI |
| Dependency and container scanning | Every build + weekly scheduled | CI |
| Secret scanning (`gitleaks`) | Every commit + full history on merge | CI |
| Automated authorisation matrix test — every endpoint × every role, asserting 200/403 | Every build | CI |
| DAST (OWASP ZAP baseline) against staging | Weekly | CI |
| Manual penetration test | Before go-live, then annually | External |
| Access review (users, roles, dormant accounts) | Quarterly | Super Admin |
| Restore drill | Quarterly | DevOps |
| Incident tabletop exercise | Annually | Security officer |

**The authorisation matrix test is the highest-value security test in this system.** It enumerates
every route from the OpenAPI schema, calls each as every role with a valid token, and asserts the
outcome against a declared matrix. A new endpoint that forgets its permission dependency fails the
build.

---

## 9.16 Security checklist for go-live

- [ ] TLS configured, HSTS enabled, certificate auto-renewal verified
- [ ] All default and seeded passwords changed; the demo admin account removed or rotated
- [ ] `APP_ENV=production`; debug off; `/docs` restricted to the internal network
- [ ] `CORS_ORIGINS` set to the exact production origin — no wildcard
- [ ] JWT switched to RS256 with the private key mounted read-only
- [ ] Database not reachable from outside the application network; app role has no DELETE/DDL
- [ ] Field-level encryption key set and backed up separately from the database
- [ ] Rate limits active at both Nginx and application layers
- [ ] All security headers present (verified with an external scanner)
- [ ] Audit logging verified end to end, including the immutability triggers
- [ ] Backups running, encrypted, off-site, and a restore drill completed
- [ ] Authorisation matrix test green for all six roles
- [ ] Penetration test findings remediated or formally risk-accepted
- [ ] Monitoring and alerting live, with an on-call rota
- [ ] Incident response and DR runbooks reviewed and accessible offline
- [ ] Compliance sign-off on PII handling and retention obtained
