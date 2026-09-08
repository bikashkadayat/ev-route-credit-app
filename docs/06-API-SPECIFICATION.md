# 6. API Specification

**Base URL:** `https://<host>/api/v1` · **Format:** JSON (UTF-8) · **Auth:** Bearer JWT
**Interactive docs:** `/api/v1/docs` (Swagger UI), `/api/v1/redoc`, `/api/v1/openapi.json`
**Sample payloads:** [../api-examples/](../api-examples/)

---

## 6.1 Conventions

### 6.1.1 Headers

| Header | Direction | Notes |
|--------|-----------|-------|
| `Authorization: Bearer <access_token>` | request | Required on everything except `/auth/login`, `/auth/refresh`, `/auth/forgot-password`, `/auth/reset-password`, `/health/*` |
| `Content-Type: application/json` | request | |
| `Idempotency-Key: <uuid>` | request | Accepted on `POST /loans`, `POST /loans/{id}/repayments`, and all `*/assess` endpoints. 24-hour replay window. |
| `X-Request-ID` | both | Client may supply; otherwise generated. Echoed on every response and present in every log line. |
| `If-Match: <version>` | request | Optional optimistic-lock guard on `PATCH` of applications and alerts |

### 6.1.2 Pagination, filtering, sorting

`GET` collections accept `?page=1&page_size=25` (`page_size` max 100) and return:

```json
{
  "items": [ ... ],
  "total": 143,
  "page": 1,
  "page_size": 25,
  "pages": 6
}
```

Sorting: `?sort=-created_at` (prefix `-` for descending). Only allow-listed fields per endpoint.
Filtering: explicit named params per endpoint (documented below). Free-text search: `?q=`.

### 6.1.3 Error envelope

Every non-2xx response has exactly this shape.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {"field": "total_distance_km", "code": "GREATER_THAN", "message": "Input should be greater than 0"},
      {"field": "avg_fare_per_trip", "code": "MISSING", "message": "Required when the route carries passengers"}
    ],
    "request_id": "01JBQ9X2K7M4N8P3R5T7V9W1Y3",
    "timestamp": "2026-09-08T10:14:22Z"
  }
}
```

### 6.1.4 Status codes

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 200 | OK | Successful read or update |
| 201 | Created | Resource created |
| 202 | Accepted | Queued background job |
| 204 | No Content | Successful delete |
| 400 | Bad Request | Malformed request or business-rule violation |
| 401 | Unauthorized | Missing, invalid or expired token |
| 403 | Forbidden | Authenticated but lacking the permission or out of row scope |
| 404 | Not Found | Unknown id, or an id outside the caller's scope |
| 409 | Conflict | Duplicate, stale version, or invalid state transition |
| 422 | Unprocessable Entity | Schema/field validation failure |
| 423 | Locked | Account locked after failed login attempts |
| 429 | Too Many Requests | Rate limited (`Retry-After` header set) |
| 500 | Internal Server Error | Unhandled — logged with the request id |
| 503 | Service Unavailable | Dependency down (database, provider circuit open) |

### 6.1.5 Application error codes

`VALIDATION_ERROR` · `INVALID_CREDENTIALS` · `ACCOUNT_LOCKED` · `ACCOUNT_INACTIVE` ·
`TOKEN_EXPIRED` · `TOKEN_REUSE_DETECTED` · `PERMISSION_DENIED` · `RESOURCE_NOT_FOUND` ·
`DUPLICATE_RESOURCE` · `STALE_VERSION` · `INVALID_STATE_TRANSITION` · `CONFIG_NOT_PUBLISHED` ·
`WEIGHTS_NOT_100` · `ROUTE_ASSESSMENT_STALE` · `KNOCKOUT_TRIGGERED` · `POLICY_LIMIT_EXCEEDED` ·
`OVERRIDE_JUSTIFICATION_REQUIRED` · `EXTERNAL_PROVIDER_UNAVAILABLE` · `IDEMPOTENCY_KEY_REUSED` ·
`RATE_LIMIT_EXCEEDED`.

### 6.1.6 Rate limits

| Endpoint group | Limit |
|----------------|-------|
| `POST /auth/login` | 5 / minute / IP **and** 10 / hour / email |
| `POST /auth/forgot-password` | 3 / hour / email |
| `*/assess`, `/scoring/*` | 30 / minute / user |
| All other authenticated | 300 / minute / user |
| Document upload | 20 / minute / user |

---

## 6.2 Authentication

### `POST /auth/login`

**Auth:** none · **Permission:** none

Request:
```json
{"email": "risk.manager@bank.com.np", "password": "Str0ng!Passw0rd", "remember_me": false}
```

Validation: `email` valid format, ≤ 150 chars; `password` 8–128 chars (length only — never reveal
policy on login).

Response `200`:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_token": "8f2a...c91d",
  "user": {
    "id": "3f1b8e42-2b6d-4b1e-9a10-0d5f7c8e1a22",
    "email": "risk.manager@bank.com.np",
    "full_name": "Sabina Karki",
    "role": {"code": "RISK_MANAGER", "name": "Risk Manager"},
    "permissions": ["route:read","route:assess","application:approve","risk:config:publish","audit:read"],
    "must_change_password": false,
    "branch_code": "HO"
  }
}
```

Errors: `401 INVALID_CREDENTIALS` (identical response and timing for unknown email and wrong
password) · `403 ACCOUNT_INACTIVE` · `423 ACCOUNT_LOCKED` (`{"retry_after_seconds": 780}`) ·
`429 RATE_LIMIT_EXCEEDED`.

### `POST /auth/refresh`
Request `{"refresh_token": "..."}` → `200` with a new access **and** refresh token (rotation).
`401 TOKEN_REUSE_DETECTED` revokes the whole session family.

### `POST /auth/logout` · `POST /auth/logout-all`
`204`. Revokes the presented token / all sessions for the user.

### `GET /auth/me`
`200` with the same `user` object as login, plus `last_login_at`.

### `POST /auth/change-password`
`{"current_password": "...", "new_password": "..."}` → `204`. Validation: ≥ 12 chars, upper + lower
+ digit + symbol, not among the last 5, not the current. Revokes all other sessions.

### `POST /auth/forgot-password` · `POST /auth/reset-password`
Always `204` regardless of whether the email exists (no account enumeration). Reset accepts
`{"token": "...", "new_password": "..."}`; token is single-use, 30-minute expiry.

---

## 6.3 Routes

### `GET /routes`

**Permission:** `route:read`
Query: `page`, `page_size`, `sort` (`route_name`, `latest_score`, `created_at`, `latest_assessed_at`),
`q`, `grade` (`A|B|C`), `status`, `province`, `district`, `min_score`, `max_score`, `stale_only` (bool).

```json
{
  "items": [{
    "id": "b2c8...", "route_code": "RT-KTM-DHU-001",
    "route_name": "Kathmandu–Dhulikhel", "origin": "Kathmandu", "destination": "Dhulikhel",
    "total_distance_km": 30.00, "route_type": "SUBURBAN",
    "charging_station_count": 6, "latest_score": 90.12, "latest_grade": "A",
    "latest_assessed_at": "2026-09-08T04:12:00Z", "is_stale": false,
    "status": "ACTIVE", "active_loan_count": 4
  }],
  "total": 10, "page": 1, "page_size": 25, "pages": 1
}
```

### `POST /routes`

**Permission:** `route:create` · Full body: [../api-examples/route-create.json](../api-examples/route-create.json)

```json
{
  "route_name": "Kathmandu–Dhulikhel",
  "origin": "Kathmandu", "destination": "Dhulikhel",
  "province": "Bagmati", "district": "Kavrepalanchok",
  "route_type": "SUBURBAN",
  "total_distance_km": 30.0,
  "road_type": "PITCH", "road_condition": "GOOD", "gradient_profile": "ROLLING",
  "pitch_road_percent": 100,
  "charging_station_count": 6, "fast_charger_count": 3,
  "avg_charging_distance_km": 5.0, "max_charging_gap_km": 12.0,
  "passenger_volume_daily": 1200, "freight_volume_daily_tons": 0,
  "estimated_daily_trips": 8, "avg_fare_per_trip": 950, "avg_freight_revenue_per_trip": 0,
  "traffic_density": "HIGH", "competition_level": "MODERATE", "operator_count": 45,
  "seasonal_risk": "LOW", "monsoon_disruption_days": 6,
  "flood_landslide_risk": "LOW", "security_risk": "LOW",
  "electricity_tariff_per_kwh": 12.0,
  "estimated_daily_operating_cost": 1800,
  "permit_required": true
}
```

**Validation rules**

| Field | Rule |
|-------|------|
| `route_name` | 3–150 chars; unique with (`origin`,`destination`) among non-deleted routes → `409 DUPLICATE_RESOURCE` |
| `total_distance_km` | `> 0`, `<= 1000` |
| `fast_charger_count` | `<= charging_station_count` |
| `max_charging_gap_km` | `<= total_distance_km`; required when `charging_station_count >= 1` |
| `estimated_daily_trips` | `> 0`, `<= 60` |
| `avg_fare_per_trip` | required when `passenger_volume_daily > 0` |
| `avg_freight_revenue_per_trip` | required when `freight_volume_daily_tons > 0` |
| `pitch_road_percent` | 0–100; required when `road_type = MIXED` |
| `monsoon_disruption_days` | 0–180 |
| `electricity_tariff_per_kwh` | 1–50 |

`201` returns the created route with `charging_station_density`, `estimated_daily_revenue` and
`estimated_daily_energy_cost` computed. Status is `DRAFT` until first assessed.

### `GET /routes/{id}` — `200` with full attributes, latest assessment summary, charging stations, linked loan count.
### `PATCH /routes/{id}` — **Permission:** `route:update`. Partial body, same validation. Editing scoring-relevant fields sets `latest_assessment.is_stale = true`.
### `DELETE /routes/{id}` — **Permission:** `route:delete`. Soft delete. `409` if the route is referenced by any non-closed loan.

### `POST /routes/{id}/assess`

**Permission:** `route:assess` · Idempotency-Key supported.

Request (all optional):
```json
{"reference_vehicle_model_id": 4, "notes": "Post-monsoon re-assessment"}
```
If `reference_vehicle_model_id` is supplied its `real_world_range_km` is used for the
gap-to-range ratio; otherwise the configured default reference range (120 km) applies.

Response `201`:
```json
{
  "id": "c7f2...", "route_id": "b2c8...",
  "total_score": 90.12, "grade": "A", "risk_level": "LOW",
  "charging_adequacy": "ADEQUATE", "revenue_potential": "HIGH",
  "recommendation": "ELIGIBLE",
  "components": [
    {"code": "CHARGING_INFRASTRUCTURE", "label": "Charging Infrastructure",
     "normalized_score": 91.13, "weight": 0.30, "weighted_score": 27.339,
     "raw_inputs": {"charging_station_density": 2.0, "gap_to_range_ratio": 0.0857, "fast_charger_share": 0.5},
     "explanation": "6 stations over 30 km (2.0 per 10 km) with a maximum gap of 12 km; 3 DC fast chargers."},
    {"code": "ROAD_QUALITY", "normalized_score": 88.25, "weight": 0.20, "weighted_score": 17.650, "...": "..."},
    {"code": "DEMAND", "normalized_score": 87.85, "weight": 0.30, "weighted_score": 26.355, "...": "..."},
    {"code": "REVENUE_POTENTIAL", "normalized_score": 98.67, "weight": 0.10, "weighted_score": 9.867, "...": "..."},
    {"code": "ROUTE_RISK", "normalized_score": 89.05, "weight": 0.10, "weighted_score": 8.905, "...": "..."}
  ],
  "risk_factors": [],
  "positive_factors": [
    {"code": "DENSE_CHARGING", "message": "6 stations over 30 km — dense charging coverage"},
    {"code": "FAST_CHARGING_AVAILABLE", "message": "3 DC fast chargers available on the corridor"},
    {"code": "HEALTHY_MARGIN", "message": "Operating margin 69% of revenue"}
  ],
  "explanation": "Kathmandu–Dhulikhel scores 90.1/100 (Class A — Low Risk). ...",
  "estimated_monthly_revenue": 197600.00,
  "estimated_monthly_profit": 136934.72,
  "config_version": {"id": 7, "config_type": "ROUTE", "version_no": 1},
  "engine_version": "route-engine@1.0.0",
  "assessed_by": {"id": "...", "name": "Ramesh Adhikari"},
  "assessed_at": "2026-09-08T04:12:00Z"
}
```

Errors: `422 VALIDATION_ERROR` listing every missing required field · `409 CONFIG_NOT_PUBLISHED`
when no ACTIVE ROUTE configuration exists · `403 PERMISSION_DENIED`.

### `GET /routes/{id}/assessments` — paginated assessment history (immutable rows).
### `GET /routes/{id}/charging-stations` — stations linked to the corridor, ordered by distance from origin.

---

## 6.4 Applicants

### `POST /applicants` — **Permission:** `applicant:create`

```json
{
  "applicant_type": "OWNER_DRIVER",
  "full_name": "Ram Bahadur Tamang",
  "date_of_birth": "1992-03-14",
  "gender": "MALE",
  "id_type": "CITIZENSHIP", "id_number": "27-01-70-04521",
  "pan_number": "301245678",
  "phone": "+9779841234567", "email": "ram.tamang@example.com",
  "province": "Bagmati", "district": "Kathmandu", "municipality": "Kathmandu Metropolitan",
  "ward_no": 16, "address_line": "Chabahil",
  "current_address_same": true,
  "driving_experience_years": 9, "commercial_driving_years": 6,
  "total_experience_years": 9,
  "licence_category": "B", "licence_expiry": "2029-03-10",
  "has_previous_ev_experience": false
}
```

**Validation:** conditional by `applicant_type` — individual types require `date_of_birth`,
`driving_experience_years`, `licence_category`, `licence_expiry`; business types require
`company_reg_number`, `company_reg_date`, `business_experience_years`. Age must be 18–70 at
application. `phone` matched against a Nepal mobile pattern. Duplicate `id_number` (hash match)
returns `409 DUPLICATE_RESOURCE` with the existing applicant's id so the UI can offer "open existing".

`201` returns the applicant with `applicant_code` assigned and `id_number` **masked**
(`27-01-70-****`). Unmasking requires `applicant:read_sensitive` and is audit-logged.

### `GET /applicants` — filters: `q` (name/phone/code), `applicant_type`, `status`, `province`, `district`.
### `GET /applicants/{id}` — full profile, current financial version, obligations, latest bureau report, documents, applications.
### `PATCH /applicants/{id}` — **Permission:** `applicant:update`.

### `POST /applicants/{id}/financials` — **Permission:** `applicant:update`

Creates a **new version** and marks it current.

```json
{
  "monthly_income": 0, "monthly_business_revenue": 145000, "other_monthly_income": 0,
  "monthly_household_expenses": 42000, "monthly_business_expenses": 18000,
  "avg_bank_balance_6m": 83000, "bank_account_count": 2, "dependants_count": 3,
  "income_proof_type": "BANK_STATEMENT", "income_verified": true, "has_guarantor": false,
  "obligations": [
    {"lender_name": "Nabil Bank", "loan_type": "PERSONAL", "original_amount": 500000,
     "outstanding_amount": 385000, "monthly_emi": 12400, "remaining_tenure_months": 34,
     "is_overdue": false, "days_past_due": 0, "source": "DECLARED"}
  ]
}
```

`total_existing_emi`, `total_existing_outstanding` and `existing_loan_count` are derived from
`obligations` and cannot be set directly. `201` returns the new version with derived totals.

### `POST /applicants/{id}/credit-report` — **Permission:** `applicant:credit_check`

```json
{"force_refresh": false}
```

Returns a cached report if one is valid (default 90 days) unless `force_refresh` is true.
`201` returns the report (see [04-SYSTEM-ARCHITECTURE.md](04-SYSTEM-ARCHITECTURE.md) §4.11).
`503 EXTERNAL_PROVIDER_UNAVAILABLE` when the circuit is open — the UI then offers manual entry via
`POST /applicants/{id}/credit-report/manual` (same body plus `is_manual_entry: true`, requires
`applicant:credit_manual_entry`).

### `POST /applicants/{id}/documents` — `multipart/form-data`
Fields: `file`, `document_type`, optional `application_id`. Max 10 MB. MIME allow-list:
`application/pdf`, `image/jpeg`, `image/png`. Magic-byte verified, not just the declared type.
`201` returns metadata; `413` if too large; `415` if the type is rejected.

### `GET /documents/{uuid}/download` — streams the file; access checked; download audit-logged.

---

## 6.5 Vehicles

### `GET /vehicle-models` — filters `category`, `brand`, `is_active`, `q`. Public to all authenticated roles.
### `POST /vehicle-models` — **Permission:** `vehicle_model:create` (Super Admin / Risk Manager).
### `POST /vehicles` — **Permission:** `vehicle:create`

```json
{
  "vehicle_model_id": 4,
  "purchase_price": 4600000,
  "is_new": true,
  "manufacture_year": 2026,
  "chassis_number": "LC0C74C4XN0123456",
  "odometer_km": 12
}
```
`registration_number`, `permit_number` and `telematics_device_id` may be added later via `PATCH`.

---

## 6.6 Loan applications

### `POST /applications` — **Permission:** `application:create`

```json
{
  "applicant_id": "a1b2...",
  "vehicle_id": "v9c8...",
  "route_id": "b2c8...",
  "requested_amount": 3200000,
  "requested_tenure_months": 60,
  "proposed_interest_rate": 13.0,
  "down_payment_amount": 1400000,
  "expected_daily_km": 240,
  "expected_operating_days_month": 26,
  "purpose": "Taxi operation on the Kathmandu–Dhulikhel corridor"
}
```

**Validation:** applicant must have a current financial profile (`400` otherwise, code
`FINANCIAL_PROFILE_MISSING`); `down_payment_amount < vehicle_on_road_price`; tenure 6–120;
rate 0–40; `expected_daily_km` ≤ 800 and warned (not blocked) if it exceeds
`route.total_distance_km × route.estimated_daily_trips × 1.5`.

`201` returns the application in `DRAFT` with `application_number`, `vehicle_on_road_price` and
`financial_profile_id` snapshotted.

### `GET /applications`
Filters: `status`, `applicant_id`, `route_id`, `decision`, `risk_grade`, `branch_code`,
`created_from`, `created_to`, `q`. Row-scoped: a Credit Officer sees their branch; HO roles see all.

### `GET /applications/{id}`
Returns the application with nested `applicant`, `financial_profile`, `vehicle`, `route`,
`route_assessment`, `credit_bureau_report`, `latest_credit_score`, `latest_loan_assessment`,
`decisions[]`, `documents[]`, `version`.

### `POST /applications/{id}/assess` — **Permission:** `application:assess` · Idempotency-Key supported

The one-call orchestration used by the UI's "Run Full Assessment" button. Runs, in order: knock-outs
→ route assessment (reuses a non-stale one, else assesses) → customer score → vehicle economics →
combined score → structuring → decision matrix. Persists everything, sets status
`UNDER_ASSESSMENT` → `PENDING_DECISION`.

Request:
```json
{"force_route_reassessment": false, "route_waiver": false, "waiver_justification": null}
```

Response `201`:
```json
{
  "application_id": "ap12...",
  "assessment_id": "la88...",
  "knockouts_triggered": [],
  "route": {"assessment_id": "c7f2...", "score": 90.12, "grade": "A", "is_reused": true,
            "assessed_at": "2026-09-08T04:12:00Z"},
  "customer": {
    "credit_score_id": "cs44...", "total_score": 75.17, "grade": "B", "risk_level": "MODERATE",
    "dti_ratio": 0.0855, "foir_ratio": 0.5876, "disposable_income": 72600.00,
    "components": [
      {"code": "CREDIT_HISTORY",   "normalized_score": 85.88, "weight": 0.30, "weighted_score": 25.764},
      {"code": "INCOME_CASHFLOW",  "normalized_score": 65.21, "weight": 0.25, "weighted_score": 16.303},
      {"code": "EXISTING_DEBT",    "normalized_score": 58.73, "weight": 0.15, "weighted_score": 8.810},
      {"code": "DOWN_PAYMENT",     "normalized_score": 68.70, "weight": 0.10, "weighted_score": 6.870},
      {"code": "EXPERIENCE",       "normalized_score": 81.40, "weight": 0.10, "weighted_score": 8.140},
      {"code": "VEHICLE_ECONOMICS","normalized_score": 90.96, "weight": 0.10, "weighted_score": 9.096}
    ]
  },
  "vehicle_economics": {
    "score": 90.96,
    "energy_cost_per_km": 2.445,
    "daily_net_contribution": 6570.22,
    "monthly_net_contribution": 168017.61,
    "payback_months": 19.05
  },
  "final": {
    "final_score": 84.31, "risk_grade": "B", "risk_level": "LOW",
    "weights": {"route": 0.40, "customer": 0.40, "vehicle": 0.20},
    "recommendation": "MANUAL_REVIEW",
    "recommended_amount": 2960000.00,
    "max_ltv_percent": 75.00, "applied_ltv_percent": 64.35,
    "recommended_tenure_months": 60, "recommended_interest_rate": 13.00,
    "estimated_emi": 67349.10, "dscr": 2.4947, "foir_post_loan": 0.5500,
    "reasons": [
      {"code": "HIGH_EMI_BURDEN", "type": "NEGATIVE", "impact": "HIGH",
       "message": "Post-loan FOIR of 58.77% at the requested amount exceeds the 50% approval threshold",
       "metric": {"name": "foir_post_loan", "value": 0.5877, "threshold": 0.50}},
      {"code": "HEALTHY_DSCR", "type": "POSITIVE", "impact": "HIGH",
       "message": "Vehicle generates 2.31x the requested EMI in net contribution",
       "metric": {"name": "dscr", "value": 2.3076, "threshold": 1.30}}
    ]
  },
  "config_versions": {"route": 7, "customer": 8, "final": 9},
  "engine_versions": {"route": "route-engine@1.0.0", "customer": "customer-engine@1.0.0", "final": "final-engine@1.0.0"},
  "assessed_at": "2026-09-08T05:02:11Z"
}
```

Errors: `409 CONFIG_NOT_PUBLISHED` · `409 ROUTE_ASSESSMENT_STALE` (when the route assessment is
older than the staleness window and `force_route_reassessment` is false) · `400 KNOCKOUT_TRIGGERED`
is **not** an error — knock-outs return `201` with `recommendation: "REJECT"` and populated
`knockouts_triggered`.

### `POST /applications/{id}/decision` — **Permission:** `application:approve`

```json
{
  "final_decision": "APPROVE",
  "approved_amount": 2960000,
  "approved_tenure_months": 60,
  "approved_interest_rate": 13.0,
  "conditions": ["Comprehensive insurance assigned to the bank for the full tenure",
                 "Telematics device installed and active before disbursement"],
  "override_justification": null
}
```

Rules: if `final_decision` differs from the system recommendation, `override_justification` of ≥ 20
characters is **required** (`400 OVERRIDE_JUSTIFICATION_REQUIRED`) and requires
`application:override`. `approved_amount` may not exceed the policy-capped
`max_ltv_percent × on_road_price` (`400 POLICY_LIMIT_EXCEEDED`). Application must be in
`PENDING_DECISION` (`409 INVALID_STATE_TRANSITION`).

`201` returns the decision record; application status becomes `APPROVED` / `REJECTED`.

### `POST /applications/{id}/submit` · `POST /applications/{id}/withdraw` — state transitions, `200`.

---

## 6.7 Scoring (stateless calculators)

These endpoints score **without persisting**, for what-if analysis on the UI.

### `POST /scoring/route` — **Permission:** `route:assess`
Body: the full route attribute set (same as `POST /routes`, no name/identity required).
Response: the same `ScoreResult` shape as `/routes/{id}/assess` minus persistence fields.

### `POST /scoring/customer` — **Permission:** `application:assess`
Body: applicant, financial, bureau, vehicle and loan-request fields inline.
Response: customer score with components, knock-outs, DTI/FOIR.

### `POST /scoring/final` — **Permission:** `application:assess`

```json
{
  "route_score": 90.12, "customer_score": 75.17,
  "vehicle": {"on_road_price": 4600000, "battery_capacity_kwh": 71.7, "real_world_range_km": 380,
              "maintenance_cost_per_km": 0.55, "battery_warranty_years": 8},
  "operation": {"daily_km": 240, "operating_days_month": 26, "daily_trips": 8,
                "avg_revenue_per_trip": 950, "electricity_tariff_per_kwh": 12,
                "terrain_factor": 0.08, "monsoon_disruption_days": 6,
                "driver_monthly_wage": 0, "annual_insurance": 85000, "annual_permit_tax": 12000},
  "loan": {"requested_amount": 3200000, "tenure_months": 60, "interest_rate": 13.0,
           "down_payment": 1400000},
  "borrower": {"total_monthly_income": 145000, "total_monthly_expenses": 60000,
               "total_existing_emi": 12400},
  "grades": {"route_grade": "A", "customer_grade": "B"}
}
```

Response: `final_score`, `risk_grade`, full economics, structuring recommendation, decision and
reasons — identical to the `final` block of `/applications/{id}/assess`. This is what powers the
"what-if" sliders (change down payment / tenure and watch DSCR and EMI move).

### `POST /scoring/emi` — utility
`{"principal": 2960000, "annual_rate": 13.0, "tenure_months": 60}` →
`{"emi": 67349.10, "total_interest": 1080946.00, "total_payable": 4040946.00}`.

---

## 6.8 Loans

### `POST /loans` — **Permission:** `loan:create` · Idempotency-Key **strongly recommended**

```json
{
  "application_id": "ap12...",
  "disbursement_date": "2026-09-15",
  "first_emi_date": "2026-10-15",
  "assigned_officer_id": "u77...",
  "vehicle_registration_number": "BA 2 CHA 4471",
  "telematics_device_id": "TRK-00891"
}
```

Rules: application must be `APPROVED` and not already linked to a loan (`409 DUPLICATE_RESOURCE`);
`first_emi_date` within 45 days of `disbursement_date`. Generates the full amortisation schedule
atomically, sets vehicle status `FINANCED` and application status `DISBURSED`.

`201` returns the loan plus a schedule summary (`installment_count`, `first_due`, `last_due`,
`total_interest`, `total_payable`).

### `GET /loans` — filters `status`, `risk_status`, `classification`, `risk_grade`, `min_dpd`, `assigned_officer_id`, `route_id`, `branch_code`, `q`.
### `GET /loans/{id}` — loan, applicant, vehicle, route, schedule summary, latest monitoring snapshot, live alerts.
### `GET /loans/{id}/schedule` — full amortisation schedule with paid/outstanding per instalment.

### `POST /loans/{id}/repayments` — **Permission:** `repayment:create` · Idempotency-Key supported

```json
{
  "payment_date": "2026-10-14",
  "amount_paid": 67349,
  "payment_mode": "BANK_TRANSFER",
  "reference_number": "TXN-889231",
  "remarks": "Full EMI for instalment 1"
}
```

Allocation order: penalty → interest → principal, applied to the **oldest unpaid instalment first**;
an excess is applied forward or recorded as an advance. Recomputes DPD, outstanding, classification
and `installments_paid` in the same transaction. `201` returns the repayment plus updated loan
totals and the affected schedule rows. `409` if the loan is `CLOSED` or `WRITTEN_OFF`.

### `GET /loans/{id}/repayments` — repayment history.
### `GET /loans/{id}/monitoring` — telemetry series, snapshots and derived trends

Query: `from`, `to` (default last 90 days), `granularity` (`DAILY` | `WEEKLY`).

```json
{
  "loan_id": "ln55...",
  "baseline_daily_km": 150.2,
  "current": {"avg_daily_km_7d": 92.5, "usage_change_percent": -38.4,
              "active_days_30d": 19, "zero_km_streak_days": 0,
              "charging_sessions_30d": 14, "charging_change_percent": -33.3,
              "latest_state_of_health": 96.2, "days_since_last_telemetry": 1,
              "behaviour_score": 51.8, "behaviour_band": "STRESSED"},
  "series": [
    {"date": "2026-09-07", "daily_km": 88.0, "trip_count": 6, "active_hours": 5.5,
     "route_deviation_percent": 4.2, "estimated_revenue": 5700.00,
     "state_of_charge": 62.0, "state_of_health": 96.2, "charging_sessions": 1}
  ]
}
```

---

## 6.9 Portfolio & dashboard

### `GET /portfolio` — **Permission:** `portfolio:read`
The risk table. Filters: `risk_status`, `risk_grade`, `classification`, `min_dpd`, `max_dpd`,
`usage_change_below`, `alert_status`, `route_id`, `assigned_officer_id`, `branch_code`.
Sort: `days_past_due`, `outstanding_principal`, `usage_change_percent`, `behaviour_score`, `final_risk_score`.

```json
{
  "items": [{
    "loan_id": "ln55...", "loan_account_number": "LN-2026-000087",
    "customer": {"id": "...", "name": "Manisha Bhandari", "code": "APP-2026-00054"},
    "vehicle": {"registration_number": "BA 3 CHA 8829", "model": "Tata Nexon EV Max"},
    "route": {"name": "Bhaktapur–Banepa", "grade": "B"},
    "principal_amount": 2890000.00, "outstanding_principal": 2786420.00,
    "overdue_amount": 173244.00,
    "final_risk_score": 78.50, "risk_grade": "B",
    "days_past_due": 47, "usage_change_percent": -38.4,
    "behaviour_score": 51.8,
    "risk_status": "RED", "open_alert_count": 2, "highest_alert_severity": "RED",
    "assigned_officer": {"id": "...", "name": "Kiran Shrestha"}
  }],
  "total": 10, "page": 1, "page_size": 25, "pages": 1,
  "aggregates": {"total_outstanding": 22614680.00, "total_overdue": 311796.00,
                 "count_red": 2, "count_yellow": 3, "count_green": 5}
}
```

### `GET /dashboard/summary` — **Permission:** `portfolio:read`
Query: `date_from`, `date_to`, `branch_code`.

```json
{
  "period": {"from": "2026-01-01", "to": "2026-09-08"},
  "kpis": {
    "total_applications": 15, "approved": 8, "rejected": 3, "manual_reviews_pending": 4,
    "approval_rate": 0.5333,
    "active_loans": 10, "total_portfolio_value": 24530000.00,
    "outstanding_amount": 22614680.00, "overdue_amount": 311796.00,
    "portfolio_at_risk_30": 0.1232,
    "yellow_alerts": 3, "red_alerts": 2,
    "avg_time_to_decision_hours": 19.4
  },
  "charts": {
    "portfolio_by_risk_grade": [{"grade": "A", "count": 3, "outstanding": 7820000.00}, {"grade": "B", "count": 5, "outstanding": 10940000.00}, {"grade": "C", "count": 2, "outstanding": 2670000.00}],
    "applications_by_decision": [{"month": "2026-06", "APPROVE": 2, "MANUAL_REVIEW": 1, "REJECT": 1}],
    "route_risk_distribution": [{"class": "A", "route_count": 4, "loan_count": 5, "exposure": 12400000.00}],
    "customer_risk_distribution": [{"grade": "A", "count": 2}, {"grade": "B", "count": 6}],
    "monthly_disbursement": [{"month": "2026-06", "amount": 5920000.00, "count": 2, "cumulative": 12300000.00}],
    "repayment_performance": [{"month": "2026-08", "on_time": 7, "paid_late": 2, "missed": 1}],
    "dpd_buckets": [{"bucket": "0", "count": 5, "outstanding": 13200000.00}, {"bucket": "1-30", "count": 3, "outstanding": 5540000.00}, {"bucket": "31-90", "count": 2, "outstanding": 2690000.00}],
    "usage_trend": [{"date": "2026-09-01", "avg_daily_km_index": 94.2}],
    "geographic_distribution": [{"province": "Bagmati", "district": "Kathmandu", "loan_count": 6, "outstanding": 14200000.00, "npl_count": 1}]
  },
  "generated_at": "2026-09-08T06:00:00Z",
  "data_as_of": "2026-09-08T02:00:00Z"
}
```

`data_as_of` reflects the materialised-view refresh time so the UI can show "as of" honestly.

---

## 6.10 Alerts

### `GET /alerts` — **Permission:** `alert:read`
Filters: `status`, `severity`, `category`, `assigned_to`, `loan_id`, `applicant_id`, `rule_code`,
`sla_status` (`ON_TRACK|DUE_SOON|BREACHED`), `trigger_from`, `trigger_to`, `unassigned` (bool).
Default sort: severity DESC, then `trigger_date` ASC (oldest RED first).

### `GET /alerts/{id}` — full alert payload including `supporting_context` and `activities[]`.

### `PATCH /alerts/{id}` — **Permission:** `alert:assign`
```json
{"status": "ACKNOWLEDGED", "assigned_to": "u77...", "version": 3}
```
`409 STALE_VERSION` if `version` does not match.

### `POST /alerts/{id}/activities` — **Permission:** `alert:read` (own assignments) 
```json
{"activity_type": "CALL_LOG", "description": "Called borrower. Vehicle in workshop for motor controller replacement; expects to resume 12 Sep. Promised payment by 15 Sep.",
 "metadata": {"contact_outcome": "REACHED", "promise_to_pay_date": "2026-09-15"}}
```

### `POST /alerts/{id}/resolve` — **Permission:** `alert:resolve`
```json
{"resolution_code": "PAYMENT_RECEIVED",
 "resolution_notes": "Full overdue amount of NPR 173,244 received on 12 Sep via bank transfer TXN-891204. Account regularised.",
 "status": "RESOLVED"}
```
Validation: `resolution_notes` ≥ 10 chars (≥ 20 for `FALSE_POSITIVE`). `200` returns the resolved
alert. `409 INVALID_STATE_TRANSITION` if already RESOLVED/SUPERSEDED.

### `POST /alerts/{id}/escalate` — **Permission:** `alert:assign`. Body `{"escalate_to": "u01...", "reason": "..."}`.

### `POST /alerts/evaluate` — **Permission:** `risk:config:publish` · `202 Accepted`
Triggers an on-demand rule evaluation. Body `{"loan_id": "ln55...", "as_of_date": "2026-09-08"}` —
omit `loan_id` for the whole book. Returns `{"job_run_id": 431, "status": "RUNNING"}`.

---

## 6.11 Administration

### `GET /admin/scoring-configs` — **Permission:** `risk:config:read`
Query `config_type`, `status`. Returns versions with component summaries.

### `POST /admin/scoring-configs` — **Permission:** `risk:config:publish` — creates a DRAFT (optionally cloning `source_config_id`).

### `PUT /admin/scoring-configs/{id}` — edit a DRAFT

```json
{
  "name": "Route scorecard v2 — increased charging weight",
  "notes": "Credit committee 2026-09-01: charging adequacy is the dominant failure mode.",
  "grade_thresholds": [
    {"grade": "A", "min": 80, "max": 100, "label": "Class A — Low Risk", "risk_level": "LOW"},
    {"grade": "B", "min": 60, "max": 79.99, "label": "Class B — Medium Risk", "risk_level": "MEDIUM"},
    {"grade": "C", "min": 0,  "max": 59.99, "label": "Class C — High Risk", "risk_level": "HIGH"}
  ],
  "components": [
    {"component_code": "CHARGING_INFRASTRUCTURE", "label": "Charging Infrastructure", "weight": 0.35, "display_order": 1, "scoring_rules": {"...": "..."}},
    {"component_code": "DEMAND", "label": "Passenger/Freight Demand", "weight": 0.28, "display_order": 2, "scoring_rules": {"...": "..."}},
    {"component_code": "ROAD_QUALITY", "label": "Road Quality", "weight": 0.18, "display_order": 3, "scoring_rules": {"...": "..."}},
    {"component_code": "REVENUE_POTENTIAL", "label": "Revenue Potential", "weight": 0.09, "display_order": 4, "scoring_rules": {"...": "..."}},
    {"component_code": "ROUTE_RISK", "label": "Route/Environmental Risk", "weight": 0.10, "display_order": 5, "scoring_rules": {"...": "..."}}
  ]
}
```

`400 WEIGHTS_NOT_100` if the active weights do not sum to exactly 1.0000 (returns the actual sum).
Only DRAFT configurations are editable (`409` otherwise).

### `POST /admin/scoring-configs/{id}/publish` — **Permission:** `risk:config:publish`
Validates weights, archives the current ACTIVE configuration of that type, activates this one,
writes an audit entry. `200` returns the activated configuration.

### `POST /admin/scoring-configs/{id}/preview` — [S] Phase 2
`{"sample_size": 50}` → grade/decision migration matrix against recent applications.

### `GET|POST|PUT /admin/risk-rules` · `POST /admin/risk-rules/{id}/toggle`
CRUD over rules and conditions. **Permission:** `risk:config:read` / `risk:config:publish`.
Deactivating a rule auto-resolves its live alerts.

### `GET|PUT /admin/settings` — **Permission:** `settings:read` / `settings:update`
```json
{"settings": [
  {"setting_key": "loan.max_ltv_percent", "setting_value": "80"},
  {"setting_key": "loan.min_down_payment_percent", "setting_value": "20"},
  {"setting_key": "loan.max_tenure_months", "setting_value": "84"},
  {"setting_key": "loan.base_interest_rate", "setting_value": "12.5"},
  {"setting_key": "underwriting.min_dscr", "setting_value": "1.25"},
  {"setting_key": "underwriting.max_foir", "setting_value": "0.55"},
  {"setting_key": "route.assessment_staleness_days", "setting_value": "180"},
  {"setting_key": "cib.report_validity_days", "setting_value": "90"},
  {"setting_key": "alert.sla_hours_red_acknowledge", "setting_value": "4"}
]}
```
Each value is validated against the setting's `value_type`, `min_value`/`max_value` and
`allowed_values`. `200` returns updated settings; every change is audit-logged individually.

### `GET|POST|PATCH /admin/users` — **Permission:** `user:*`. `POST /admin/users/{id}/deactivate` revokes all sessions immediately.
### `GET /admin/roles` — roles with their permission sets.

### `GET /admin/audit-logs` — **Permission:** `audit:read`
Filters: `user_id`, `action`, `entity_type`, `entity_id`, `date_from`, `date_to`, `status`, `q`.
Read-only. Export via `GET /admin/audit-logs/export?format=csv` (watermarked, audit-logged).

---

## 6.12 Reports & exports

| Endpoint | Permission | Output |
|----------|-----------|--------|
| `GET /reports/risk?format=csv\|pdf` | `report:read` | Portfolio risk report with grade distribution and PAR |
| `GET /reports/portfolio?format=csv\|pdf` | `report:read` | Loan-level portfolio schedule |
| `GET /reports/applications?format=csv\|pdf` | `report:read` | Application funnel with decisions and reasons |
| `GET /applications/{id}/report?format=pdf` | `application:read` | Full credit appraisal memo for the file |
| `GET /routes/{id}/report?format=pdf` | `route:read` | Route assessment certificate |

All exports accept the same filters as their screen, are streamed, and write an audit entry
recording the filter set and row count.

---

## 6.13 Health & operations

| Endpoint | Auth | Response |
|----------|------|----------|
| `GET /health/live` | none | `{"status": "ok"}` |
| `GET /health/ready` | none | `{"status":"ok","checks":{"database":"ok","migrations":"head","active_configs":{"ROUTE":7,"CUSTOMER":8,"FINAL":9}}}` — `503` if any check fails |
| `GET /metrics` | internal network only | Prometheus text format |
| `GET /admin/jobs` | `settings:read` | Recent `job_runs` with status and counts |
| `POST /admin/jobs/{job_name}/run` | `settings:update` | `202` — manual trigger with optional `as_of_date` |

---

## 6.14 Endpoint summary

| Method | Path | Permission | Scope |
|--------|------|-----------|-------|
| POST | `/auth/login` | — | [M] |
| POST | `/auth/refresh` · `/auth/logout` · `/auth/logout-all` | — / authenticated | [M] |
| GET | `/auth/me` | authenticated | [M] |
| POST | `/auth/change-password` · `/auth/forgot-password` · `/auth/reset-password` | mixed | [M] |
| GET/POST | `/routes` | `route:read` / `route:create` | [M] |
| GET/PATCH/DELETE | `/routes/{id}` | `route:read` / `route:update` / `route:delete` | [M] |
| POST | `/routes/{id}/assess` | `route:assess` | [M] |
| GET | `/routes/{id}/assessments` · `/routes/{id}/charging-stations` | `route:read` | [M] |
| GET/POST | `/applicants` | `applicant:read` / `applicant:create` | [M] |
| GET/PATCH | `/applicants/{id}` | `applicant:read` / `applicant:update` | [M] |
| POST | `/applicants/{id}/financials` | `applicant:update` | [M] |
| POST | `/applicants/{id}/credit-report` | `applicant:credit_check` | [M] |
| POST | `/applicants/{id}/documents` | `applicant:update` | [M] |
| GET | `/documents/{uuid}/download` | `applicant:read` | [M] |
| GET/POST | `/vehicle-models` · `/vehicles` | `vehicle*` | [M] |
| GET/POST | `/applications` | `application:read` / `application:create` | [M] |
| GET | `/applications/{id}` | `application:read` | [M] |
| POST | `/applications/{id}/assess` | `application:assess` | [M] |
| POST | `/applications/{id}/decision` | `application:approve` | [M] |
| POST | `/applications/{id}/submit` · `/withdraw` | `application:create` | [M] |
| POST | `/scoring/route` · `/scoring/customer` · `/scoring/final` · `/scoring/emi` | assess perms | [M] |
| GET/POST | `/loans` | `portfolio:read` / `loan:create` | [M] |
| GET | `/loans/{id}` · `/loans/{id}/schedule` · `/loans/{id}/repayments` · `/loans/{id}/monitoring` | `portfolio:read` | [M] |
| POST | `/loans/{id}/repayments` | `repayment:create` | [M] |
| GET | `/portfolio` · `/dashboard/summary` | `portfolio:read` | [M] |
| GET | `/alerts` · `/alerts/{id}` | `alert:read` | [M] |
| PATCH | `/alerts/{id}` | `alert:assign` | [M] |
| POST | `/alerts/{id}/activities` · `/resolve` · `/escalate` | `alert:*` | [M] |
| POST | `/alerts/evaluate` | `risk:config:publish` | [M] |
| GET/POST/PUT | `/admin/scoring-configs` | `risk:config:*` | [M] |
| POST | `/admin/scoring-configs/{id}/publish` | `risk:config:publish` | [M] |
| POST | `/admin/scoring-configs/{id}/preview` | `risk:config:publish` | [S] |
| GET/POST/PUT | `/admin/risk-rules` | `risk:config:*` | [M] |
| GET/PUT | `/admin/settings` | `settings:*` | [M] |
| GET/POST/PATCH | `/admin/users` · `/admin/roles` | `user:*` | [M] |
| GET | `/admin/audit-logs` (+ export) | `audit:read` | [M] |
| GET | `/reports/*` | `report:read` | [M] CSV / [S] PDF |
| GET | `/health/*` · `/metrics` | — / internal | [M] |
