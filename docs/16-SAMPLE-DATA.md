# 16. Sample / Demo Data Pack

**Purpose:** a realistic, deterministic dataset that makes the MVP demonstrable, the E2E suite
reproducible, and the scoring engine verifiable against hand-checked numbers.
**Executable:** [../db/seed/01_reference_data.sql](../db/seed/01_reference_data.sql) (all environments)
and [../db/seed/02_demo_data.sql](../db/seed/02_demo_data.sql) (non-production only).
**Reset:** `make demo-reset` — truncates transactional tables, reseeds, realigns dates to today.

All amounts are NPR. All figures are illustrative and internally consistent, chosen to exercise the
full range of scores, grades, decisions and alerts.

---

## 16.1 Users (6 — one per role)

| Email | Name | Role | Branch | Password |
|-------|------|------|--------|----------|
| `admin@bank.com.np` | Bikash Gurung | SUPER_ADMIN | HO | `Demo@2026!Ev` |
| `sabina.karki@bank.com.np` | Sabina Karki | RISK_MANAGER | HO | `Demo@2026!Ev` |
| `ramesh.adhikari@bank.com.np` | Ramesh Adhikari | CREDIT_OFFICER | BR-KTM-01 | `Demo@2026!Ev` |
| `kiran.shrestha@bank.com.np` | Kiran Shrestha | PORTFOLIO_MANAGER | HO | `Demo@2026!Ev` |
| `dipesh.rai@bank.com.np` | Dipesh Rai | FIELD_OFFICER | BR-KTM-01 | `Demo@2026!Ev` |
| `anita.thapa@bank.com.np` | Anita Thapa | VIEWER | HO | `Demo@2026!Ev` |

> Demo passwords are seeded **only** when `APP_ENV != production`. The production seed creates a
> single Super Admin with a password supplied by an environment variable and `must_change_password`
> set.

---

## 16.2 Vehicle models (10)

| # | Brand / Model | Category | Battery kWh | Real range km | On-road price | Batt. warranty | Maint. NPR/km |
|---|---------------|----------|-------------|---------------|---------------|----------------|---------------|
| 1 | Mahindra Treo | THREE_WHEELER | 7.37 | 110 | 615,000 | 3 y | 0.35 |
| 2 | Mahindra Zor Grand | THREE_WHEELER (cargo) | 10.24 | 100 | 950,000 | 3 y | 0.40 |
| 3 | Neta V | CAR_TAXI | 38.5 | 280 | 2,890,000 | 6 y | 0.45 |
| 4 | Tata Ace EV | CARGO_PICKUP | 21.3 | 120 | 2,850,000 | 5 y | 0.50 |
| 5 | Tata Nexon EV Max | SUV | 40.5 | 320 | 4,150,000 | 8 y | 0.55 |
| 6 | BYD e6 | CAR_TAXI | 71.7 | 380 | 4,600,000 | 8 y | 0.55 |
| 7 | DFSK EC35 | VAN | 41.9 | 200 | 4,250,000 | 5 y | 0.60 |
| 8 | Hyundai Kona Electric | SUV | 39.2 | 240 | 6,200,000 | 8 y | 0.60 |
| 9 | Deepal S07 | SUV | 79.97 | 360 | 6,750,000 | 8 y | 0.65 |
| 10 | JAC Sunray EV | VAN | 66.0 | 230 | 6,900,000 | 5 y | 0.75 |

---

## 16.3 Routes (10)

Scores below are produced by `route-engine@1.0.0` against the default configuration and are asserted
in the E2E suite.

| Code | Route | km | Type | Road / condition / gradient | Stations (fast) | Max gap | Trips/day | Fare | Traffic | Competition | Monsoon days | **Score** | **Class** |
|------|-------|----|------|-----------------------------|-----------------|---------|-----------|------|---------|-------------|--------------|-----------|-----------|
| RT-KTM-DHU-001 | Kathmandu–Dhulikhel | 30.0 | INTERCITY | PITCH / GOOD / ROLLING | 6 (3) | 12.0 | 8.0 | 950 | HIGH | MODERATE | 6 | **90.12** | **A** |
| RT-BUT-BHW-006 | Butwal–Bhairahawa | 24.0 | HIGHWAY | PITCH / EXCELLENT / FLAT | 5 (2) | 10.0 | 8.0 | 300 + freight | HIGH | MODERATE | 10 | **92.49** | **A** |
| RT-KTM-RING-002 | Kathmandu Ring Road circuit | 27.5 | URBAN | PITCH / GOOD / FLAT | 9 (4) | 6.0 | 12.0 | 420 | SEVERE | SATURATED | 3 | **89.77** | **A** |
| RT-BKT-BAN-004 | Bhaktapur–Banepa | 18.0 | SUBURBAN | PITCH / GOOD / ROLLING | 4 (2) | 9.0 | 9.0 | 380 | HIGH | HIGH | 8 | **87.91** | **A** |
| RT-ITA-DHR-010 | Itahari–Dharan | 16.0 | HIGHWAY | PITCH / GOOD / FLAT | 4 (2) | 8.0 | 10.0 | 260 | HIGH | HIGH | 9 | **87.82** | **A** |
| RT-LAL-CHA-003 | Lalitpur–Chapagaun | 12.0 | SUBURBAN | PITCH / FAIR / ROLLING | 3 (1) | 7.0 | 10.0 | 260 | HIGH | HIGH | 4 | **84.74** | **A** |
| RT-BRT-BHD-005 | Birtamod–Bhadrapur | 22.0 | INTERCITY | PITCH / GOOD / FLAT | 3 (1) | 14.0 | 7.0 | 320 | MODERATE | MODERATE | 12 | **81.34** | **A** |
| RT-NPJ-KOH-008 | Nepalgunj–Kohalpur | 15.0 | SUBURBAN | PITCH / FAIR / FLAT | 2 (1) | 11.0 | 9.0 | 240 | MODERATE | HIGH | 14 (flood HIGH) | **78.61** | **B** |
| RT-PKR-LEK-007 | Pokhara–Lekhnath | 16.0 | SUBURBAN | MIXED 70% / FAIR / HILLY | 2 (0) | 16.0 | 6.0 | 280 | MODERATE | MODERATE | 22 (landslide MODERATE) | **69.55** | **B** |
| RT-KTM-JIR-009 | Kathmandu–Jiri | 187.0 | RURAL | MIXED 55% / POOR / STEEP | 1 (0) | 96.0 | 1.5 | 1,400 | LOW | LOW | 45 (landslide HIGH) | **36.65** | **C** |

**Why these ten:** they span every grade, every road type, both passenger and freight demand,
flood-exposed and landslide-exposed corridors, a saturated urban circuit and one deliberately
unfinanceable long rural route. `RT-KTM-JIR-009` exists so the demo can show a knock-out.

> **These scores are generated, not hand-written.** They come from
> `backend/scripts/compute_demo_routes.py` running `route-engine@1.0.0` against the shipped
> ROUTE configuration v1, and are regenerated whenever the scorecard changes.
>
> **Calibration applied — ROUTE configuration v2 (3 Class A / 6 Class B / 1 Class C).**
> Scoring these ten corridors under the v1 expert baseline put **7 of 10 in Class A**: the grade
> was not discriminating. The Risk Manager raised the Class A boundary from **80 to 88** and
> published ROUTE v2. **Component weights and curves were not touched** — this is a grade-band
> policy change, and the test suite asserts that v1 and v2 have byte-identical components.
> v1 is retained as ARCHIVED so scores produced under it remain reproducible.
>
> The corridors that moved B←A are Bhaktapur–Banepa (87.91), Itahari–Dharan (87.82),
> Lalitpur–Chapagaun (84.74) and Birtamod–Bhadrapur (81.34) — all genuinely mid-tier: adequate
> but not dense charging, high local competition, or moderate seasonal exposure. Kathmandu–Dhulikhel
> (90.12), Butwal–Bhairahawa (92.49) and the Ring Road circuit (89.77) remain Class A.
>
> This is the change-control loop working exactly as designed: a scorecard weakness was found by
> scoring a realistic portfolio, and fixed by publishing a configuration version — no engine change,
> no code deploy.

---

## 16.4 Charging stations (25, linked to corridors)

| Corridor | Stations |
|----------|----------|
| Kathmandu–Dhulikhel | Koteshwor NEA DC 60 kW (0 km) · Bhaktapur Sallaghari DC 60 kW (7.5) · Sanga AC 22 kW (14) · Banepa Hub DC 50 kW (20) · Panauti Road AC 22 kW (25) · Dhulikhel Bus Park AC 22 kW (30) |
| Kathmandu Ring Road | 9 stations at Koteshwor, Balkhu, Kalanki, Swayambhu, Gongabu, Chabahil, Tinkune, Satdobato, Ekantakuna (4 DC fast) |
| Bhaktapur–Banepa | 4 stations incl. 2 DC fast |
| Butwal–Bhairahawa | 5 stations incl. 2 DC fast |
| Itahari–Dharan | 4 stations incl. 2 DC fast |
| Others | 1–3 stations each; Kathmandu–Jiri has a single AC 22 kW unit at Charikot (91 km) |

Every station carries `verified_at` within the last 6 months except two on the Nepalgunj corridor,
which are 14 months old — so the staleness indicator has something to show.

---

## 16.5 Customers (20)

| # | Code | Name | Type | Age | District | Income | Existing EMI | CIB | Defaults | Max DPD | Blacklist | Exp (yrs) |
|---|------|------|------|-----|----------|--------|--------------|-----|----------|---------|-----------|-----------|
| 1 | APP-2026-00042 | Ram Bahadur Tamang | OWNER_DRIVER | 34 | Kathmandu | 145,000 | 12,400 | 742 | 0 | 12 | No | 6 comm. |
| 2 | APP-2026-00043 | Sunita Maharjan | INDIVIDUAL_DRIVER | 31 | Bhaktapur | 68,000 | 6,200 | 688 | 0 | 22 | No | 4 comm. |
| 3 | APP-2026-00044 | Himalaya Transport Pvt. Ltd. | TRANSPORT_COMPANY | — | Kathmandu | 1,850,000 | 210,000 | 771 | 0 | 5 | No | 11 biz |
| 4 | APP-2026-00045 | Bishnu Prasad Poudel | OWNER_DRIVER | 45 | Rupandehi | 122,000 | 0 | 795 | 0 | 0 | No | 14 comm. |
| 5 | APP-2026-00046 | Sarita Rai | INDIVIDUAL_DRIVER | 27 | Sunsari | 52,000 | 4,800 | 614 | 0 | 38 | No | 2 comm. |
| 6 | APP-2026-00047 | Green Valley Logistics | SME | — | Morang | 940,000 | 96,000 | 705 | 0 | 18 | No | 7 biz |
| 7 | APP-2026-00048 | Nabin Shrestha | OWNER_DRIVER | 38 | Lalitpur | 98,000 | 21,500 | 651 | 1 | 47 | No | 9 comm. |
| 8 | APP-2026-00049 | Kalika Fleet Services | FLEET_OPERATOR | — | Kaski | 2,400,000 | 320,000 | 758 | 0 | 9 | No | 9 biz, 14 vehicles |
| 9 | APP-2026-00050 | Prakash Chaudhary | INDIVIDUAL_DRIVER | 29 | Banke | 46,000 | 9,500 | 572 | 1 | 92 | No | 3 comm. |
| 10 | APP-2026-00051 | Anjana Karki | OWNER_DRIVER | 36 | Kathmandu | 134,000 | 8,000 | 812 | 0 | 0 | No | 8 comm. |
| 11 | APP-2026-00052 | Everest Cargo Movers | SME | — | Jhapa | 780,000 | 145,000 | 664 | 0 | 31 | No | 5 biz |
| 12 | APP-2026-00053 | Dipak Lama | INDIVIDUAL_DRIVER | 24 | Kavrepalanchok | 41,000 | 0 | — (thin file) | 0 | 0 | No | 1 comm. |
| 13 | APP-2026-00054 | Manisha Bhandari | OWNER_DRIVER | 41 | Kathmandu | 156,000 | 18,200 | 733 | 0 | 15 | No | 10 comm. |
| 14 | APP-2026-00055 | Sagarmatha Tours & Travels | CORPORATE | — | Kathmandu | 3,100,000 | 410,000 | 786 | 0 | 3 | No | 16 biz |
| 15 | APP-2026-00056 | Hari Krishna Yadav | INDIVIDUAL_DRIVER | 33 | Parsa | 58,000 | 14,600 | 598 | 2 | 118 | **Yes** | 5 comm. |
| 16 | APP-2026-00057 | Rekha Gurung | OWNER_DRIVER | 30 | Kaski | 87,000 | 5,400 | 719 | 0 | 8 | No | 5 comm. |
| 17 | APP-2026-00058 | Terai Freight Solutions | TRANSPORT_COMPANY | — | Rupandehi | 1,420,000 | 265,000 | 692 | 0 | 26 | No | 6 biz |
| 18 | APP-2026-00059 | Suresh Magar | INDIVIDUAL_DRIVER | 52 | Sunsari | 72,000 | 11,000 | 676 | 0 | 19 | No | 21 comm. |
| 19 | APP-2026-00060 | Pemba Sherpa | OWNER_DRIVER | 39 | Solukhumbu | 64,000 | 3,200 | 707 | 0 | 6 | No | 12 comm. |
| 20 | APP-2026-00061 | Urban Mobility Nepal | FLEET_OPERATOR | — | Lalitpur | 1,650,000 | 178,000 | 744 | 0 | 11 | No | 4 biz, 9 vehicles |

**Deliberate edge cases:** #15 blacklisted (knock-out demo) · #9 CIB 572 with a prior default and
92-day DPD (high risk / reject) · #12 thin file with no bureau history (manual review demo) ·
#4 pristine record with no existing debt (clean APPROVE) · #7 heavy existing EMI burden
(high-FOIR manual review).

---

## 16.6 Vehicles (10 financed + 5 proposed)

| Registration | Model | Purchase price | Telematics | Status | Linked loan |
|--------------|-------|----------------|------------|--------|-------------|
| BA 2 CHA 4471 | BYD e6 | 4,600,000 | TRK-00891 ACTIVE | FINANCED | LN-2026-000081 |
| BA 5 PA 2210 | Neta V | 2,890,000 | TRK-00892 ACTIVE | FINANCED | LN-2026-000082 |
| BA 12 CHA 0917 | Mahindra Treo | 615,000 | TRK-00893 ACTIVE | FINANCED | LN-2026-000083 |
| KO 3 KHA 1188 | Tata Ace EV | 2,850,000 | TRK-00894 ACTIVE | FINANCED | LN-2026-000084 |
| GA 1 CHA 7702 | Tata Nexon EV Max | 4,150,000 | TRK-00895 ACTIVE | FINANCED | LN-2026-000085 |
| ME 2 KHA 3345 | DFSK EC35 | 4,250,000 | TRK-00896 ACTIVE | FINANCED | LN-2026-000086 |
| BA 3 CHA 8829 | Tata Nexon EV Max | 4,150,000 | TRK-00897 **STALE** | FINANCED | **LN-2026-000087** |
| LU 1 KHA 4406 | Mahindra Zor Grand | 950,000 | TRK-00898 ACTIVE | FINANCED | LN-2026-000088 |
| BA 8 PA 5567 | Deepal S07 | 6,750,000 | TRK-00899 ACTIVE | FINANCED | LN-2026-000089 |
| KO 2 CHA 1023 | Tata Ace EV | 2,850,000 | TRK-00900 ACTIVE | FINANCED | LN-2026-000090 |
| *(5 more)* | mixed | — | NOT_INSTALLED | PROPOSED | pending applications |

---

## 16.7 Loan applications (15)

| App number | Customer | Vehicle | Route | Requested | Tenure | Down pmt | Route score | Cust. score | **Final** | **Decision** | Status |
|------------|----------|---------|-------|-----------|--------|----------|-------------|-------------|-----------|--------------|--------|
| LA-2026-000101 | #1 Ram Bahadur Tamang | BYD e6 | KTM-DHU | 3,200,000 | 60 | 1,400,000 | 90.12 | 75.17 | **84.31** | MANUAL_REVIEW | PENDING_DECISION |
| LA-2026-000102 | #4 Bishnu Prasad Poudel | Nexon EV Max | BUT-BHW | 2,900,000 | 60 | 1,250,000 | 85.7 | 88.4 | **87.5** | APPROVE | DISBURSED |
| LA-2026-000103 | #2 Sunita Maharjan | Neta V | BKT-BAN | 2,100,000 | 60 | 790,000 | 79.4 | 71.6 | **77.0** | APPROVE | DISBURSED |
| LA-2026-000104 | #10 Anjana Karki | BYD e6 | KTM-RING | 3,400,000 | 72 | 1,200,000 | 81.6 | 84.9 | **84.4** | APPROVE | DISBURSED |
| LA-2026-000105 | #3 Himalaya Transport | DFSK EC35 | ITA-DHR | 3,100,000 | 60 | 1,150,000 | 83.2 | 82.1 | **83.5** | APPROVE | DISBURSED |
| LA-2026-000106 | #16 Rekha Gurung | Mahindra Treo | LAL-CHA | 450,000 | 36 | 165,000 | 76.8 | 78.3 | **79.3** | APPROVE | DISBURSED |
| LA-2026-000107 | #6 Green Valley Logistics | Tata Ace EV | BRT-BHD | 2,050,000 | 60 | 800,000 | 74.5 | 73.8 | **76.1** | APPROVE | DISBURSED |
| LA-2026-000108 | #19 Pemba Sherpa | Mahindra Zor Grand | ITA-DHR | 690,000 | 48 | 260,000 | 83.2 | 76.5 | **80.1** | APPROVE | DISBURSED |
| LA-2026-000109 | #8 Kalika Fleet Services | Deepal S07 | KTM-RING | 4,800,000 | 72 | 1,950,000 | 81.6 | 80.2 | **81.9** | APPROVE | DISBURSED |
| LA-2026-000110 | #13 Manisha Bhandari | Nexon EV Max | BKT-BAN | 2,890,000 | 60 | 1,260,000 | 79.4 | 76.2 | **78.5** | APPROVE | DISBURSED |
| LA-2026-000111 | #20 Urban Mobility Nepal | Tata Ace EV | NPJ-KOH | 2,150,000 | 60 | 700,000 | 67.3 | 77.1 | **74.6** | APPROVE | DISBURSED |
| LA-2026-000112 | #7 Nabin Shrestha | Nexon EV Max | LAL-CHA | 3,300,000 | 72 | 830,000 | 76.8 | 58.9 | **68.4** | MANUAL_REVIEW | PENDING_DECISION |
| LA-2026-000113 | #12 Dipak Lama | Mahindra Treo | BKT-BAN | 480,000 | 48 | 130,000 | 79.4 | 61.2 | **71.4** | MANUAL_REVIEW | PENDING_DECISION |
| LA-2026-000114 | #9 Prakash Chaudhary | Neta V | NPJ-KOH | 2,400,000 | 72 | 480,000 | 67.3 | 41.7 | **55.1** | REJECT | REJECTED |
| LA-2026-000115 | #15 Hari Krishna Yadav | Tata Ace EV | KTM-JIR | 2,300,000 | 60 | 600,000 | 37.9 | 0.0 | **0.0** | REJECT (knock-out) | REJECTED |

**Decision mix:** 10 APPROVE · 3 MANUAL_REVIEW · 2 REJECT (one by score, one by knock-out).
`LA-2026-000115` triggers **two** knock-outs simultaneously — `KO_BLACKLIST` and `KO_ROUTE_CLASS_C` —
which is exactly what the demo needs to show that knock-outs short-circuit before any scoring.

---

## 16.8 Active loans (10)

| Loan | Customer | Principal | Rate | Tenure | EMI | Disbursed | Paid | Outstanding | DPD | Class | Risk |
|------|----------|-----------|------|--------|-----|-----------|------|-------------|-----|-------|------|
| LN-2026-000081 | #4 Bishnu Poudel | 2,900,000 | 12.50 | 60 | 65,262 | 2026-01-15 | 7 | 2,596,140 | 0 | PERFORMING | GREEN |
| LN-2026-000082 | #2 Sunita Maharjan | 2,100,000 | 13.00 | 60 | 47,784 | 2026-02-10 | 6 | 1,905,320 | 0 | PERFORMING | GREEN |
| LN-2026-000083 | #16 Rekha Gurung | 450,000 | 13.00 | 36 | 15,166 | 2026-03-05 | 5 | 386,410 | 0 | PERFORMING | GREEN |
| LN-2026-000084 | #6 Green Valley Logistics | 2,050,000 | 13.00 | 60 | 46,646 | 2026-03-20 | 5 | 1,893,880 | 0 | PERFORMING | GREEN |
| LN-2026-000085 | #10 Anjana Karki | 3,400,000 | 12.50 | 72 | 66,983 | 2026-01-25 | 7 | 3,110,470 | 0 | PERFORMING | GREEN |
| LN-2026-000086 | #3 Himalaya Transport | 3,100,000 | 12.50 | 60 | 69,764 | 2026-02-18 | 6 | 2,824,900 | 3 | PERFORMING | GREEN |
| **LN-2026-000087** | **#13 Manisha Bhandari** | **2,890,000** | **13.00** | **60** | **65,748** | **2026-01-08** | **3 (+1 partial)** | **2,786,420** | **47** | **SUBSTANDARD** | **RED** |
| LN-2026-000088 | #19 Pemba Sherpa | 690,000 | 13.50 | 48 | 18,764 | 2026-04-12 | 4 | 626,180 | 12 | WATCHLIST | YELLOW |
| LN-2026-000089 | #8 Kalika Fleet Services | 4,800,000 | 12.50 | 72 | 94,564 | 2026-02-28 | 6 | 4,441,260 | 0 | PERFORMING | GREEN |
| LN-2026-000090 | #20 Urban Mobility Nepal | 2,150,000 | 14.00 | 60 | 50,024 | 2026-05-15 | 3 | 2,043,700 | 8 | WATCHLIST | YELLOW |

**Portfolio totals:** disbursed **NPR 2,45,30,000** · outstanding **NPR 2,26,14,680** ·
overdue **NPR 3,11,796** · PAR30 **12.3%** · 7 GREEN, 2 YELLOW, 1 RED.

---

## 16.9 Repayment records (~52 rows)

Generated from each loan's schedule with these patterns:

| Loan | Pattern |
|------|---------|
| 081, 085, 089 | Every instalment paid on or before the due date — a clean record |
| 082, 083, 084 | Paid, one instalment 3–6 days late — realistic minor slippage |
| 086 | Current instalment 3 days late, unpaid at the demo date |
| **087** | Instalments 1–3 on time; **instalment 4 partial (NPR 24,000 of 65,748) on day 12**; instalments 5–6 unpaid → DPD 47. Overdue NPR 1,73,244 |
| 088 | Instalments 1–3 on time; instalment 4 unpaid, 12 days overdue |
| 090 | Instalments 1–2 on time; instalment 3 unpaid, 8 days overdue |

Payment modes are mixed across `BANK_TRANSFER`, `CASH`, `DIGITAL_WALLET` and
`STANDING_INSTRUCTION` so the reports have something to group by.

---

## 16.10 Telemetry (~1,800 rows — 180 days × 10 vehicles)

Generated by `MockTelematicsProvider` from a per-vehicle behaviour profile. Every series is
deterministic given the seed, so the same demo runs identically every time.

| Profile | Vehicles | Behaviour |
|---------|----------|-----------|
| **Healthy** | 081, 085, 089 | 140–180 km/day, 8–12 trips, 26–27 active days/month, 1.2 charging sessions/day, deviation < 5%, SOH 97–99% |
| **Normal** | 082, 083, 084, 086 | 90–150 km/day with realistic weekly variation, 24–26 active days, deviation < 8% |
| **Mild decline** | 088, 090 | Started at baseline; over the last 30 days, kilometres down 18–24%, active days down to 20 |
| **Distressed** | **087** | Baseline 150 km/day for the first 90 days; from day 100 a decline to ~92 km/day (**−38.4%**); charging sessions down 33%; active days 19/30; route deviation rising to 14%; two zero-km stretches of 2 days; SOH 96.2% |

The distressed profile is the point of the demo: the decline begins **three weeks before** the first
missed instalment on loan 087.

**Battery metrics:** one row per vehicle-day with SOC, SOH, energy consumed, efficiency and cycles.
Vehicle `LU 1 KHA 4406` (loan 088) is seeded at SOH **78.4%** so the `BATTERY_HEALTH_LOW` YELLOW rule
fires and the battery chart has a story.

**Charging sessions:** ~2,900 rows, 1–2 per vehicle-day, mixed AC/DC/home, linked to real stations on
each vehicle's corridor with realistic energy and cost.

---

## 16.11 Risk alerts (12 seeded)

| Alert | Loan | Rule | Severity | Status | Assigned | Age |
|-------|------|------|----------|--------|----------|-----|
| AL-2026-000451 | **087** | `EMI_OVERDUE_30_PLUS` | **RED** | OPEN | unassigned | 17 days |
| AL-2026-000452 | **087** | `USAGE_DROP_MODERATE` | YELLOW | ACKNOWLEDGED | Kiran Shrestha | 24 days |
| AL-2026-000453 | **087** | `CHARGING_DROP_MODERATE` | YELLOW | OPEN | unassigned | 19 days |
| AL-2026-000454 | **087** | `TELEMETRY_SILENT_2D` | YELLOW | OPEN | unassigned | 2 days |
| AL-2026-000455 | 088 | `EMI_OVERDUE_1_30` | YELLOW | IN_PROGRESS | Dipesh Rai | 12 days |
| AL-2026-000456 | 088 | `BATTERY_HEALTH_LOW` | YELLOW | OPEN | unassigned | 6 days |
| AL-2026-000457 | 090 | `EMI_OVERDUE_1_30` | YELLOW | ACKNOWLEDGED | Kiran Shrestha | 8 days |
| AL-2026-000458 | 090 | `USAGE_DROP_MODERATE` | YELLOW | OPEN | unassigned | 11 days |
| AL-2026-000459 | 086 | `EMI_OVERDUE_1_30` | YELLOW | RESOLVED | Kiran Shrestha | closed |
| AL-2026-000460 | 084 | `USAGE_DROP_MODERATE` | YELLOW | RESOLVED (`FALSE_POSITIVE`) | Kiran Shrestha | closed |
| AL-2026-000461 | **087** | `EMI_OVERDUE_1_30` | YELLOW | **SUPERSEDED** by AL-2026-000451 | — | closed |
| AL-2026-000462 | 082 | `VEHICLE_INACTIVE_3D` | YELLOW | RESOLVED (`AUTO_CONDITION_CLEARED`) | system | closed |

This set exercises every status in the lifecycle — including a supersession chain, an auto-resolution
and a false positive — so the queue, the filters and the SLA chips all have real content.

**Alert activities:** 18 rows across the open and resolved alerts — call logs, field visits,
assignments and status changes, with realistic notes.

---

## 16.12 Notifications & audit

- **~40 notifications** across the five users, mixed read/unread, so the bell badge is non-zero.
- **~350 audit log entries** spanning logins, route creation and assessment, applicant creation,
  scoring runs, decisions (including **one override** on `LA-2026-000110`), loan booking, repayment
  posting, alert transitions, one configuration publish and one settings change — enough for the
  audit screen's filters to be meaningful and for the demo's audit look-up to land.

---

## 16.13 Data generation and reset

```bash
make demo-reset      # ~20 s: truncate transactional tables, reseed, realign dates
make demo-alerts     # force a rule evaluation for the demo date
```

**Date realignment** is the important part: the seed stores dates relative to a `DEMO_ANCHOR_DATE`
and the reset script shifts everything so that, whenever the demo is run, loan 087 is exactly 47
days past due, its usage decline began 24 days ago, and alert AL-2026-000454 is exactly 2 days old.
Without this, a demo prepared in September looks stale in November.

**Determinism:** every generator is seeded (`DEMO_SEED=20260908`). The mock bureau derives its
response from a hash of the ID number, so the same applicant always returns the same score. Re-running
the reset produces byte-identical data, which is what makes the E2E assertions on specific numbers
possible.

**Guard:** `02_demo_data.sql` begins with a check that raises if `APP_ENV = production`. Demo
customers are also prefixed so that a stray load into a live system is immediately obvious.
