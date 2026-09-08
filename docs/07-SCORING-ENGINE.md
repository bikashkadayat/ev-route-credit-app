# 7. Scoring Engine Specification

**Audience:** Risk analysts, backend engineers, QA
**Engine versions covered:** `route-engine@1.0.0`, `customer-engine@1.0.0`, `final-engine@1.0.0`

> **Nature of this scorecard.** These are **expert-judgement weights**, not coefficients fitted to
> observed defaults — no EV default history exists in this market yet. They are deliberately
> transparent and conservative. Every component of every score is persisted so the book can be
> statistically re-fitted once roughly 300 loans have matured. Calibration is a standing quarterly
> agenda item, not an afterthought.

---

> **Verification status.** Every figure in the worked examples below is produced by the
> implementation in `backend/app/engines/` and asserted, sub-factor by sub-factor, in
> `backend/tests/unit/engines/`. The curve tables and weights are normative; the summary figures
> are derived from them. An earlier draft of this document carried hand-computed totals that were
> slightly off (route 90.14 vs 90.12, customer 75.10 vs 75.17, vehicle economics 92.13 vs 90.96,
> final 84.52 vs 84.31) — the curves were right and the arithmetic was not. The values here are the
> engine's, and the test suite fails if this document and the code ever disagree again.

---

## 7.1 Engine principles

| # | Principle | Consequence |
|---|-----------|-------------|
| 1 | **Pure functions.** `score(payload, config) → result` | No DB, no clock, no randomness. Same inputs ⇒ same output forever. |
| 2 | **Configuration is data.** | Weights, curves and thresholds come from `scoring_configurations`; the engine contains no magic numbers. |
| 3 | **Everything is `Decimal`.** | Quantised to 2dp with `ROUND_HALF_UP`. Never `float`. |
| 4 | **Every score explains itself.** | Component breakdown + factor lists + reason codes are part of the return type, not an optional extra. |
| 5 | **Bounded output.** | Sub-scores and totals clamp to [0, 100]. |
| 6 | **Missing data is explicit.** | A missing required input raises; a missing optional input scores at the configured `default_score` and adds a `DATA_INCOMPLETE` factor. |
| 7 | **Weights sum to 1.0000.** | Enforced at publish time by a database trigger and by an engine assertion. |

---

## 7.2 Normalisation primitives

Every raw input becomes a 0–100 sub-score through one of three rule kinds (grammar in
[05-DATABASE-DESIGN.md](05-DATABASE-DESIGN.md) §5.5).

### 7.2.1 `BANDED` — categorical or step

```
score = bands[value] if value in bands else default_score
```

### 7.2.2 `LINEAR` — piecewise-linear interpolation

Given ordered points `(x₀,y₀) … (xₙ,yₙ)` and input `v`:

```
if v ≤ x₀      → y₀
if v ≥ xₙ      → yₙ
else, for the segment where xᵢ ≤ v < xᵢ₊₁:
    score = yᵢ + (yᵢ₊₁ − yᵢ) × (v − xᵢ) / (xᵢ₊₁ − xᵢ)
```

Piecewise-linear was chosen over a logistic/sigmoid curve deliberately: a credit committee can read
the point table and check it against the policy document. A risk analyst can move one point without
understanding curve algebra.

### 7.2.3 `COMPOSITE` — weighted sub-factors inside one component

```
component_score = Σ (sub_factorᵢ.weight × normalise(sub_factorᵢ.rule, inputs))
```

### 7.2.4 Aggregation and grading

```
total_score = Σ (componentᵢ.normalized_score × componentᵢ.weight)      # 0–100
grade       = first band in config.grade_thresholds where min ≤ total_score ≤ max
```

---

## 7.3 MODULE 1 — Route Scoring Engine

### 7.3.1 Component weights (default configuration v1)

| Code | Component | Default weight | Rationale |
|------|-----------|---------------|-----------|
| `CHARGING_INFRASTRUCTURE` | Charging Infrastructure | **30%** | The binding operational constraint for an EV. A vehicle that cannot reliably recharge cannot earn, regardless of demand. |
| `DEMAND` | Passenger / Freight Demand | **30%** | Determines whether the revenue assumption is real. |
| `ROAD_QUALITY` | Road Quality | **20%** | Drives energy consumption, tyre/suspension cost, downtime and effective range. |
| `REVENUE_POTENTIAL` | Revenue Potential | **10%** | Margin per operating day; partially correlated with demand, hence the lower weight. |
| `ROUTE_RISK` | Route / Environmental Risk | **10%** | Monsoon, landslide, security, seasonality — episodic rather than continuous. |
| | **Total** | **100%** | |

### 7.3.2 `CHARGING_INFRASTRUCTURE` (composite, 30%)

| Sub-factor | Weight | Input | Curve (x → y) |
|-----------|--------|-------|---------------|
| Station density | 40% | `charging_station_density` (stations per 10 km) | 0→0, 0.5→40, 1.0→70, 2.0→90, 3.0→100 |
| Gap-to-range ratio | 35% | `max_charging_gap_km / vehicle_real_range_km` | 0→100, 0.4→85, 0.6→60, 0.8→30, 1.0→0 |
| Fast-charger share | 25% | `fast_charger_count / charging_station_count` | 0→20, 0.25→60, 0.5→85, 1.0→100 |

Derived inputs:

```
charging_station_density = charging_station_count / (total_distance_km / 10)
gap_to_range_ratio       = max_charging_gap_km / usable_range_km
                           (a route-only assessment uses the configured reference vehicle:
                            underwriting.reference_battery_kwh = 24 kWh and
                            underwriting.reference_range_km = 140 km. An application-linked
                            assessment uses the financed vehicle's real_world_range_km.)
fast_charger_share       = fast_charger_count / NULLIF(charging_station_count, 0)   -- 0 when no stations
```

**Charging adequacy verdict** (reported separately from the score, because it is a gate, not a slider):

| Verdict | Condition |
|---------|-----------|
| `ADEQUATE` | `gap_to_range_ratio ≤ 0.6` **and** `charging_station_count ≥ 2` |
| `MARGINAL` | `0.6 < gap_to_range_ratio ≤ 0.85` **or** `charging_station_count = 1` |
| `INADEQUATE` | `gap_to_range_ratio > 0.85` **or** `charging_station_count = 0` |

An `INADEQUATE` verdict adds a `CRITICAL` risk factor and caps the component score at 25 regardless
of the curve output — a configurable cap (`charging.inadequate_cap`, default 25).

> **Known limitation (raised during implementation, carried as a Phase 2 change).** The verdict
> considers only the *maximum gap* against usable range. It does not consider whether the route's
> **total distance** exceeds usable range. Kathmandu–Jiri is the worked example: 187 km of route
> against a 140 km reference range, served by a single 22 kW AC charger at the 91 km mark. The
> maximum gap is 96 km (68.6% of range), so the rule returns `MARGINAL` even though the vehicle
> physically cannot complete the corridor on one charge and cannot recover meaningfully from a slow
> charger mid-route. The *score* is unaffected — the charging component still lands at 23.2 on its
> own curves and the route is correctly Class C — but the label understates the problem. Recommended
> fix: add a fourth condition, `total_distance_km / usable_range > 1.0 AND fast_charger_count == 0
> ⇒ INADEQUATE`. This is a configuration and rule change, not an engine change.

### 7.3.3 `ROAD_QUALITY` (composite, 20%)

| Sub-factor | Weight | Input | Mapping |
|-----------|--------|-------|---------|
| Road condition | 45% | `road_condition` | EXCELLENT 100 · GOOD 85 · FAIR 65 · POOR 35 · VERY_POOR 10 |
| Road type | 30% | `road_type` | PITCH 100 · MIXED = `pitch_road_percent` scaled: `40 + 0.6 × pitch%` · GRAVEL 45 · OFF_ROAD 15 |
| Gradient | 25% | `gradient_profile` | FLAT 100 · ROLLING 80 · HILLY 55 · STEEP 30 |

Gradient feeds the vehicle-economics energy multiplier as well:
`terrain_factor` = FLAT 0.00 · ROLLING 0.08 · HILLY 0.18 · STEEP 0.30.

### 7.3.4 `DEMAND` (composite, 30%)

| Sub-factor | Weight | Input | Curve / mapping |
|-----------|--------|-------|-----------------|
| Trip volume | 40% | `estimated_daily_trips` | 0→0, 2→30, 4→55, 6→75, 8→88, 12→100 |
| Passenger/freight volume | 30% | `demand_index` (see below) | 0→0, 200→40, 600→70, 1200→88, 2500→100 |
| Traffic density | 15% | `traffic_density` | LOW 55 · MODERATE 85 · HIGH 100 · SEVERE 70 |
| Competition | 15% | `competition_level` | LOW 100 · MODERATE 75 · HIGH 45 · SATURATED 20 |

```
demand_index = passenger_volume_daily + (freight_volume_daily_tons × 10)
```

Note the deliberate non-monotonicity on traffic: moderate-to-high traffic signals a busy corridor
(good for a passenger operator, and EVs suffer far less than ICE in stop-go conditions), while
`SEVERE` congestion destroys trips per day.

### 7.3.5 `REVENUE_POTENTIAL` (composite, 10%)

| Sub-factor | Weight | Input | Curve |
|-----------|--------|-------|-------|
| Daily margin | 50% | `daily_margin` = `est_daily_revenue − est_daily_operating_cost − est_daily_energy_cost` (NPR) | 0→0, 500→30, 1000→55, 2000→80, 3500→95, 5000→100 |
| Margin ratio | 30% | `daily_margin / est_daily_revenue` | 0→0, 0.15→35, 0.25→60, 0.35→82, 0.50→100 |
| Revenue per km | 20% | `est_daily_revenue / (total_distance_km × est_daily_trips)` | 0→0, 8→35, 15→65, 25→88, 40→100 |

Derived revenue (used when the officer does not override):

```
est_daily_revenue = estimated_daily_trips × (avg_fare_per_trip × passenger_load_factor
                                             + avg_freight_revenue_per_trip)
passenger_load_factor default 1.0 (already an average-fare figure)

est_daily_energy_cost = daily_km × energy_cost_per_km
daily_km              = estimated_daily_trips × total_distance_km
energy_cost_per_km    = (battery_kwh / real_range_km) × electricity_tariff_per_kwh × (1 + terrain_factor)
```

**Revenue potential label:** `HIGH` if the component ≥ 75, `MODERATE` if 50–74, `LOW` below 50.

### 7.3.6 `ROUTE_RISK` (composite, 10%) — higher score = *lower* risk

| Sub-factor | Weight | Input | Mapping |
|-----------|--------|-------|---------|
| Flood / landslide | 35% | `flood_landslide_risk` | NONE 100 · LOW 85 · MODERATE 60 · HIGH 30 · SEVERE 5 |
| Seasonal disruption | 30% | `monsoon_disruption_days` | 0→100, 10→85, 25→65, 45→40, 75→15, 120→0 |
| Seasonal risk rating | 20% | `seasonal_risk` | NONE 100 · LOW 85 · MODERATE 60 · HIGH 30 · SEVERE 5 |
| Security | 15% | `security_risk` | LOW 100 · MODERATE 60 · HIGH 20 |

### 7.3.7 Route classification

**Active: ROUTE configuration v2.**

| Grade | Range | Label | Risk level | Recommendation |
|-------|-------|-------|-----------|----------------|
| **A** | 88–100 | Class A — Low Risk | LOW | `ELIGIBLE` — eligible for EV financing at standard terms |
| **B** | 60–87.99 | Class B — Medium Risk | MEDIUM | `ELIGIBLE_WITH_CONDITIONS` — eligible with tighter LTV/tenure |
| **C** | 0–59.99 | Class C — High Risk | HIGH | `NOT_ELIGIBLE` — not eligible without a Risk Manager waiver |

**Calibration history**

| Version | Status | Class A boundary | Reason |
|---------|--------|------------------|--------|
| v1 | ARCHIVED | 80 | Expert baseline. Scoring the ten reference corridors put **7 of 10 in Class A**, so the grade did not discriminate. |
| v2 | **ACTIVE** | **88** | First calibration review. Boundary raised; **every component weight and curve is unchanged from v1**. Distribution became 3 A / 6 B / 1 C. |

This is the change-control loop working as designed: a scorecard problem was found by scoring a
realistic portfolio, and it was fixed by publishing a new configuration version — no engine change,
no deploy. v1 remains queryable so any score produced under it can still be recomputed and
explained (Doc 02 §2.4.4).

Stored in `scoring_configurations.grade_thresholds`; fully editable, and additional grades may be
added without a code change.

### 7.3.8 Factor generation rules

Factors are produced by declarative checks, so the explanation cannot drift from the score.

| Code | Type | Condition | Severity | Message template |
|------|------|-----------|----------|------------------|
| `NO_CHARGING_STATIONS` | risk | `charging_station_count = 0` | CRITICAL | "No charging infrastructure on the corridor" |
| `CHARGING_GAP_EXCEEDS_RANGE` | risk | `gap_to_range_ratio > 0.85` | CRITICAL | "Longest charging gap ({gap} km) is {pct}% of usable range" |
| `SPARSE_CHARGING` | risk | `density < 0.5` | HIGH | "Only {n} station(s) over {km} km" |
| `POOR_ROAD_CONDITION` | risk | `road_condition IN (POOR, VERY_POOR)` | HIGH | "Road condition rated {value}; higher wear and downtime expected" |
| `STEEP_GRADIENT` | risk | `gradient_profile = STEEP` | MEDIUM | "Steep gradient increases energy consumption by ~30%" |
| `HIGH_MONSOON_DISRUPTION` | risk | `monsoon_disruption_days > 30` | HIGH | "{n} disruption days per year (~{pct}% of operating days)" |
| `LANDSLIDE_EXPOSURE` | risk | `flood_landslide_risk IN (HIGH, SEVERE)` | HIGH | "Corridor exposed to flood/landslide risk" |
| `SATURATED_COMPETITION` | risk | `competition_level = SATURATED` | MEDIUM | "Saturated operator competition compresses fares" |
| `THIN_MARGIN` | risk | `margin_ratio < 0.15` | HIGH | "Operating margin only {pct}% of revenue" |
| `LOW_TRIP_VOLUME` | risk | `estimated_daily_trips < 3` | MEDIUM | "Only {n} trips/day projected" |
| `SEVERE_CONGESTION` | risk | `traffic_density = SEVERE` | MEDIUM | "Severe congestion reduces achievable trips" |
| `DENSE_CHARGING` | positive | `density ≥ 1.5` | — | "{n} stations over {km} km — dense charging coverage" |
| `FAST_CHARGING_AVAILABLE` | positive | `fast_charger_count ≥ 2` | — | "{n} DC fast chargers available on the corridor" |
| `EXCELLENT_ROAD` | positive | `road_condition IN (EXCELLENT, GOOD)` and `road_type = PITCH` | — | "Fully pitched road in {condition} condition" |
| `STRONG_DEMAND` | positive | `demand_index ≥ 800` | — | "High passenger/freight volume on the corridor" |
| `HEALTHY_MARGIN` | positive | `margin_ratio ≥ 0.30` | — | "Operating margin {pct}% of revenue" |
| `SHORT_ROUTE_EV_FRIENDLY` | positive | `total_distance_km ≤ 40` and density ≥ 1.0 | — | "Short corridor well within single-charge range" |
| `NO_SEASONAL_DISRUPTION` | positive | `monsoon_disruption_days ≤ 5` | — | "Year-round operability" |

### 7.3.9 Worked example — Kathmandu–Dhulikhel

**Inputs**

| Field | Value |
|-------|-------|
| `total_distance_km` | 30.0 |
| `road_type` / `road_condition` / `gradient_profile` | PITCH / GOOD / ROLLING |
| `charging_station_count` / `fast_charger_count` | 6 / 3 |
| `max_charging_gap_km` | 12.0 |
| reference range (`real_world_range_km`) | 140 |
| `estimated_daily_trips` | 8 |
| `passenger_volume_daily` / `freight_volume_daily_tons` | 1,200 / 0 |
| `traffic_density` / `competition_level` | HIGH / MODERATE |
| `avg_fare_per_trip` | NPR 950 |
| `estimated_daily_operating_cost` | NPR 1,800 |
| `electricity_tariff_per_kwh` | NPR 12 |
| battery / range (reference vehicle) | 24 kWh / 140 km |
| `monsoon_disruption_days` | 6 |
| `flood_landslide_risk` / `seasonal_risk` / `security_risk` | LOW / LOW / LOW |

**Derived**

```
density              = 6 / (30/10)            = 2.00 stations per 10 km
gap_to_range_ratio   = 12 / 140               = 0.085714
fast_charger_share   = 3 / 6                  = 0.50
daily_km             = 8 × 30                 = 240 km
terrain_factor       = ROLLING                = 0.08
energy_cost_per_km   = (24/140) × 12 × 1.08   = 2.221714 NPR/km
est_daily_energy     = 240 × 2.221714         = NPR 533.21
est_daily_revenue    = 8 × 950                = NPR 7,600.00
daily_margin         = 7600 − 1800 − 533.21   = NPR 5,266.79
margin_ratio         = 5266.79 / 7600         = 0.6930
revenue_per_km       = 7600 / 240             = NPR 31.6667
demand_index         = 1200 + 0 × 10          = 1200
```

**Component scoring**

| Component | Sub-factor | Value | Sub-score | Sub-weight | Contribution |
|-----------|-----------|-------|-----------|-----------|--------------|
| **Charging** | density | 2.00 | 90.00 | 0.40 | 36.000 |
| | gap/range | 0.085714 | 96.79 | 0.35 | 33.875 |
| | fast share | 0.50 | 85.00 | 0.25 | 21.250 |
| | **= 91.13** | | | | |
| **Road** | condition GOOD | — | 85.0 | 0.45 | 38.25 |
| | type PITCH | — | 100.0 | 0.30 | 30.00 |
| | gradient ROLLING | — | 80.0 | 0.25 | 20.00 |
| | **= 88.25** | | | | |
| **Demand** | trips | 8 | 88.0 | 0.40 | 35.20 |
| | demand index | 1200 | 88.0 | 0.30 | 26.40 |
| | traffic HIGH | — | 100.0 | 0.15 | 15.00 |
| | competition MODERATE | — | 75.0 | 0.15 | 11.25 |
| | **= 87.85** | | | | |
| **Revenue** | daily margin | 5,266.79 | 100.00 | 0.50 | 50.000 |
| | margin ratio | 0.6930 | 100.00 | 0.30 | 30.000 |
| | revenue/km | 31.6667 | 93.33 | 0.20 | 18.667 |
| | **= 98.67** | | | | |
| **Risk** | flood LOW | — | 85.00 | 0.35 | 29.750 |
| | monsoon 6 days | 6 | 91.00 | 0.30 | 27.300 |
| | seasonal LOW | — | 85.00 | 0.20 | 17.000 |
| | security LOW | — | 100.00 | 0.15 | 15.000 |
| | **= 89.05** | | | | |

**Weighted total**

```
Route Score = 91.13×0.30 + 88.25×0.20 + 87.85×0.30 + 98.67×0.10 + 89.05×0.10
            = 27.339 + 17.650 + 26.355 +  9.867 +  8.905
            = 90.116  →  quantised 90.12  →  Grade A (≥ 88 under ROUTE v2)
```

Component scores are quantised to two decimal places *before* weighting, because that is how they
are persisted (`route_score_components.normalized_score` is `NUMERIC(5,2)`). This makes the stored
breakdown and the stored total reconcile exactly rather than approximately.

Charging adequacy: `ADEQUATE` (ratio 0.0857 ≤ 0.6, 6 stations).
Revenue potential: `HIGH` (98.67 ≥ 75).
Recommendation: `ELIGIBLE`.

**Generated factors**
Positive (6): `DENSE_CHARGING`, `FAST_CHARGING_AVAILABLE`, `EXCELLENT_ROAD`, `STRONG_DEMAND`,
`HEALTHY_MARGIN`, `SHORT_ROUTE_EV_FRIENDLY`.
Risk: none.

`NO_SEASONAL_DISRUPTION` does **not** fire: its threshold is ≤ 5 disruption days and this corridor
has 6.

**Explanation (generated by the engine, verbatim):** *"Kathmandu-Dhulikhel scores 90.12/100
(Class A - Low Risk). Charging Infrastructure is the strongest contributor at 91.13/100. There are 6
charging stations across 30 km (2 per 10 km) including 3 DC fast chargers, with a maximum gap of
12 km - 8.57% of the 140 km reference range, rated adequate. Demand is 8 projected trips/day with
moderate operator competition. Projected daily margin is NPR 5,266.79 on NPR 7,600.00 revenue
(69.3%), at an energy cost of NPR 2.22/km. Seasonal exposure is 6 disruption days per year."*

---

## 7.4 MODULE 2 — Customer Scoring Engine

### 7.4.1 Knock-out rules (evaluated **before** scoring)

If any fires, the engine returns immediately with `total_score = 0`, `grade = E`,
`recommendation = REJECT`, and no component scoring is performed. This keeps a rejection cheap,
unambiguous and impossible to override by a high score elsewhere.

| Code | Condition | Default | Editable |
|------|-----------|---------|----------|
| `KO_BLACKLIST` | `cib.is_blacklisted = true` | on | enable/disable |
| `KO_ACTIVE_DEFAULT` | `cib.previous_default_count > 0 AND cib.current_overdue_amount > 0` | on | enable/disable |
| `KO_BUREAU_SCORE_FLOOR` | `cib.bureau_score < 550` | 550 | value |
| `KO_AGE_LIMIT` | age at maturity `< 21` or `> 65` (individual types) | 21 / 65 | values |
| `KO_MIN_DOWN_PAYMENT` | `down_payment / on_road_price < 0.20` | 20% | value |
| `KO_MAX_AMOUNT` | `requested_amount > 5,000,000` | NPR 5,000,000 | value |
| `KO_MAX_TENURE` | `requested_tenure_months > 84` | 84 | value |
| `KO_ROUTE_CLASS_C` | route grade = C and no waiver | on | enable/disable |
| `KO_LICENCE_INVALID` | driver-operated and licence missing or expired | on | enable/disable |
| `KO_VEHICLE_AGE` | used vehicle older than 3 years | 3 years | value |

### 7.4.2 Component weights (default configuration v1)

| Code | Component | Weight | Rationale |
|------|-----------|--------|-----------|
| `CREDIT_HISTORY` | Credit / bureau history | **30%** | Past repayment behaviour remains the strongest single predictor. |
| `INCOME_CASHFLOW` | Income & cash flow | **25%** | Capacity to pay from all sources. |
| `EXISTING_DEBT` | Existing debt burden | **15%** | Crowding-out of the new EMI. |
| `DOWN_PAYMENT` | Down payment / equity | **10%** | Skin in the game; drives recovery on default. |
| `EXPERIENCE` | Business / driving experience | **10%** | Operational competence on the route. |
| `VEHICLE_ECONOMICS` | Vehicle economics | **10%** | Does the asset itself service the loan? |
| | **Total** | **100%** | |

### 7.4.3 `CREDIT_HISTORY` (composite, 30%)

| Sub-factor | Weight | Input | Curve / mapping |
|-----------|--------|-------|-----------------|
| Bureau score | 50% | `bureau_score` (300–900) | 550→0, 600→25, 650→45, 700→65, 750→82, 800→92, 900→100 |
| Repayment record | 20% | `max_dpd_last_24m` | 0→100, 15→80, 30→55, 60→30, 90→10, 180→0 |
| Default history | 15% | `previous_default_count` | 0→100, 1→45, 2→15, 3→0 |
| Credit depth | 10% | `credit_history_months` | 0→20, 6→40, 12→60, 24→80, 48→95, 72→100 |
| Enquiry behaviour | 5% | `enquiries_last_6m` | 0→100, 1→95, 2→85, 4→60, 6→30, 10→0 |

**No credit history case.** If `credit_history_months = 0` and no bureau score exists, the component
scores at the configured `thin_file_score` (default 45) and a `THIN_CREDIT_FILE` risk factor is
added. The decision matrix additionally prevents a thin file from reaching APPROVE automatically —
it lands in MANUAL REVIEW.

### 7.4.4 `INCOME_CASHFLOW` (composite, 25%)

| Sub-factor | Weight | Input | Curve |
|-----------|--------|-------|-------|
| Income adequacy | 35% | `total_monthly_income` (NPR) | 15k→0, 25k→30, 40k→55, 60k→75, 100k→92, 200k→100 |
| Disposable income vs EMI | 35% | `disposable_income / proposed_emi` | 0.8→0, 1.0→25, 1.3→55, 1.6→75, 2.0→90, 3.0→100 |
| Income stability | 20% | `income_proof_type` + `income_verified` | AUDITED_FINANCIALS 100 · BANK_STATEMENT verified 90 · SALARY_SLIP verified 85 · BANK_STATEMENT unverified 60 · SELF_DECLARED 35 |
| Banking behaviour | 10% | `avg_bank_balance_6m / proposed_emi` | 0→0, 1→40, 2→65, 4→85, 8→100 |

```
disposable_income = total_monthly_income − total_monthly_expenses − total_existing_emi
```

### 7.4.5 `EXISTING_DEBT` (composite, 15%) — higher score = lower burden

| Sub-factor | Weight | Input | Curve |
|-----------|--------|-------|-------|
| FOIR post-loan | 50% | `(existing_emi + proposed_emi) / total_income` | 0.20→100, 0.35→85, 0.45→65, 0.55→40, 0.65→15, 0.80→0 |
| DTI pre-loan | 25% | `existing_emi / total_income` | 0→100, 0.15→85, 0.25→65, 0.40→35, 0.55→10, 0.70→0 |
| Obligation count | 15% | `existing_loan_count` | 0→100, 1→90, 2→75, 3→55, 4→35, 6→10 |
| Overdue obligations | 10% | count of `existing_obligations.is_overdue` | 0→100, 1→40, 2→10, 3→0 |

### 7.4.6 `DOWN_PAYMENT` (single rule, 10%)

| Input | Curve |
|-------|-------|
| `down_payment / on_road_price` | 0.20→30, 0.25→50, 0.30→68, 0.40→85, 0.50→95, 0.60→100 |

(Below 0.20 the `KO_MIN_DOWN_PAYMENT` knock-out has already fired.)

### 7.4.7 `EXPERIENCE` (composite, 10%)

Applied per applicant type; the sub-factor weights differ by type, which is itself configuration.

**Individual driver / owner-driver**

| Sub-factor | Weight | Input | Curve / mapping |
|-----------|--------|-------|-----------------|
| Commercial driving years | 50% | `commercial_driving_years` | 0→10, 1→35, 3→60, 5→80, 8→92, 12→100 |
| Total driving years | 25% | `driving_experience_years` | 0→10, 2→40, 5→70, 10→90, 15→100 |
| Prior EV experience | 15% | `has_previous_ev_experience` | true 100 · false 50 |
| Licence validity | 10% | months to `licence_expiry` | 0→0, 6→50, 12→80, 24→100 |

**Corporate / fleet / SME / transport company**

| Sub-factor | Weight | Input | Curve |
|-----------|--------|-------|-------|
| Business operating years | 55% | `business_experience_years` | 0→10, 1→30, 3→60, 5→78, 10→95, 15→100 |
| Fleet size | 25% | `fleet_size` | 0→30, 1→50, 3→70, 5→85, 10→95, 20→100 |
| Prior EV experience | 20% | `has_previous_ev_experience` | true 100 · false 45 |

### 7.4.8 `VEHICLE_ECONOMICS` (composite, 10%)

The same calculator feeds the combined engine (§7.5.2); here it is normalised to 0–100.

| Sub-factor | Weight | Input | Curve |
|-----------|--------|-------|-------|
| DSCR | 45% | `monthly_net_contribution / proposed_emi` | 0.8→0, 1.0→25, 1.25→55, 1.5→75, 2.0→92, 3.0→100 |
| Energy cost efficiency | 20% | `energy_cost_per_km` (NPR) | 1.0→100, 2.0→85, 3.0→65, 4.5→35, 6.0→10 |
| Payback period | 20% | `payback_months` | 12→100, 24→85, 36→65, 48→40, 60→20, 84→0 |
| Warranty coverage | 15% | `battery_warranty_months ÷ tenure_months` | 0.5→20, 0.75→55, 1.0→85, 1.25→100 |

### 7.4.9 Credit rating bands (default)

| Grade | Range | Label | Interpretation |
|-------|-------|-------|----------------|
| **A** | 85–100 | Excellent | Strong on every dimension |
| **B** | 70–84.99 | Good | Sound, minor weaknesses |
| **C** | 55–69.99 | Moderate | Mixed indicators; requires review |
| **D** | 40–54.99 | High Risk | Material weaknesses |
| **E** | 0–39.99 | Very High Risk | Not acceptable |

### 7.4.10 Customer factor rules (extract)

| Code | Type | Condition | Severity |
|------|------|-----------|----------|
| `HIGH_EMI_BURDEN` | risk | `foir_post_loan > 0.55` | HIGH |
| `MODERATE_BUREAU_SCORE` | risk | `600 ≤ bureau_score < 680` | MEDIUM |
| `WEAK_BUREAU_SCORE` | risk | `bureau_score < 600` | HIGH |
| `RECENT_DELINQUENCY` | risk | `max_dpd_last_24m > 30` | HIGH |
| `THIN_CREDIT_FILE` | risk | `credit_history_months < 6` | MEDIUM |
| `UNVERIFIED_INCOME` | risk | `income_proof_type = SELF_DECLARED` | MEDIUM |
| `MULTIPLE_OBLIGATIONS` | risk | `existing_loan_count ≥ 3` | MEDIUM |
| `CREDIT_HUNGRY` | risk | `enquiries_last_6m ≥ 4` | MEDIUM |
| `LOW_DSCR` | risk | `dscr < 1.25` | HIGH |
| `INEXPERIENCED_OPERATOR` | risk | commercial experience < 1 year | MEDIUM |
| `LICENCE_EXPIRING` | risk | licence expires within tenure start + 6 months | LOW |
| `STRONG_CREDIT_RECORD` | positive | `bureau_score ≥ 750 AND previous_default_count = 0` | — |
| `CLEAN_REPAYMENT` | positive | `max_dpd_last_24m = 0` | — |
| `STRONG_DOWN_PAYMENT` | positive | ratio ≥ 0.35 | — |
| `HEALTHY_DSCR` | positive | `dscr ≥ 1.5` | — |
| `EXPERIENCED_OPERATOR` | positive | commercial experience ≥ 5 years | — |
| `VERIFIED_INCOME` | positive | `income_verified = true` | — |
| `PRIOR_EV_EXPERIENCE` | positive | `has_previous_ev_experience` | — |

---

## 7.5 Combined Financing Risk Engine

### 7.5.1 Formula

```
Final Risk Score = (Route Score × w_route)
                 + (Customer Score × w_customer)
                 + (Vehicle Economics Score × w_vehicle)

defaults: w_route = 0.40, w_customer = 0.40, w_vehicle = 0.20   (must sum to 1.0000)
```

Grade bands (`FINAL` configuration, default):

| Grade | Range | Risk level |
|-------|-------|-----------|
| A | 85–100 | Very Low |
| B | 70–84.99 | Low |
| C | 55–69.99 | Moderate |
| D | 40–54.99 | High |
| E | 0–39.99 | Very High |

### 7.5.2 Vehicle economics calculator (exact)

```
# 1. Energy
energy_per_km_kwh   = battery_capacity_kwh / real_world_range_km
energy_cost_per_km  = energy_per_km_kwh × electricity_tariff_per_kwh × (1 + terrain_factor)

# 2. Daily operation
daily_km            = expected_daily_km                     # officer input, sanity-checked
                                                            # against route distance × trips
daily_energy_cost   = daily_km × energy_cost_per_km
daily_maintenance   = daily_km × maintenance_cost_per_km    # from vehicle_models
daily_driver_cost   = driver_monthly_wage / operating_days  # 0 for owner-drivers
daily_fixed_accrual = (annual_insurance + annual_permit + annual_tax) / (12 × operating_days)

daily_gross_revenue = estimated_daily_trips × avg_revenue_per_trip
daily_net_contribution = daily_gross_revenue
                       − daily_energy_cost
                       − daily_maintenance
                       − daily_driver_cost
                       − daily_fixed_accrual

# 3. Monthly, haircut for route disruption
seasonal_factor      = 1 − (monsoon_disruption_days / 365)
monthly_net_contribution = daily_net_contribution × operating_days_per_month × seasonal_factor

# 4. Coverage
DSCR            = monthly_net_contribution / proposed_emi
payback_months  = (on_road_price − down_payment) / monthly_net_contribution
```

### 7.5.3 EMI (reducing balance, equal monthly instalment)

```
r = annual_interest_rate / 12 / 100
n = tenure_months
P = principal

EMI = P × r × (1 + r)^n / ((1 + r)^n − 1)        for r > 0
EMI = P / n                                       for r = 0
```

Rounded to 2 decimals, `ROUND_HALF_UP`. The final instalment absorbs any rounding residue so that
`Σ principal_due = P` exactly.

### 7.5.4 Loan structuring

```
# 1. LTV cap for the grade (policy table, configurable)
max_ltv        = min(ltv_by_grade[final_grade], absolute_ltv_ceiling)   # e.g. min(0.80, 0.80)

# 2. Amount the collateral supports
amount_by_ltv  = on_road_price × max_ltv

# 3. Amount the borrower's income supports
emi_by_foir    = (foir_cap × total_monthly_income) − total_existing_emi   # foir_cap default 0.55
emi_by_dscr    = monthly_net_contribution / dscr_min                      # dscr_min default 1.25
emi_capacity   = min(emi_by_foir, emi_by_dscr)
amount_by_emi  = principal_from_emi(emi_capacity, rate, tenure)
               = EMI × ((1+r)^n − 1) / (r × (1+r)^n)

# 4. Recommendation
recommended_amount = min(requested_amount,
                         amount_by_ltv,
                         amount_by_emi,
                         product_max_amount)
recommended_amount = max(recommended_amount, product_min_amount)  # else REJECT as unviable

# 5. Tenure
recommended_tenure = min(requested_tenure,
                         max_tenure_by_grade[final_grade],
                         battery_warranty_months + warranty_grace_months)

# 6. Pricing
recommended_rate   = base_rate + grade_premium[final_grade]
```

**Policy tables (defaults, all editable in Admin → Loan Rules)**

| Final grade | Max LTV | Max tenure | Rate premium |
|-------------|---------|-----------|--------------|
| A | 80% | 84 months | +0.00% |
| B | 75% | 72 months | +0.50% |
| C | 70% | 60 months | +1.50% |
| D | 60% | 48 months | +3.00% |
| E | — | — | not offered |

Base rate default 12.50%. Absolute LTV ceiling 80%. Product min/max amount NPR 100,000 / 5,000,000.

### 7.5.5 Decision matrix

Evaluated top-down; the **first matching row wins**.

| # | Condition | Decision |
|---|-----------|----------|
| 1 | any knock-out triggered | **REJECT** |
| 2 | `final_score ≥ 75` AND `customer_grade ∈ {A,B}` AND `route_grade ∈ {A,B}` AND `dscr ≥ 1.30` AND `foir_post_loan ≤ 0.50` AND credit file not thin | **APPROVE** |
| 3 | `final_score < 50` OR `customer_grade = E` OR `dscr < 1.00` | **REJECT** |
| 4 | otherwise | **MANUAL REVIEW** |

All numeric parameters live in `scoring_configurations.decision_rules` (FINAL config) as JSON:

```json
{
  "approve": {
    "min_final_score": 75,
    "allowed_customer_grades": ["A", "B"],
    "allowed_route_grades": ["A", "B"],
    "min_dscr": 1.30,
    "max_foir": 0.50,
    "require_credit_history": true
  },
  "reject": {
    "max_final_score": 50,
    "disallowed_customer_grades": ["E"],
    "min_dscr": 1.00
  }
}
```

### 7.5.6 Reason generation

Every decision carries an ordered reason list. Reasons are typed so the UI can colour them and the
report can group them.

```json
{
  "recommendation": "MANUAL_REVIEW",
  "reasons": [
    {"code": "HIGH_EMI_BURDEN",      "type": "NEGATIVE", "impact": "HIGH",
     "message": "Post-loan FOIR of 58% exceeds the 50% approval threshold",
     "metric": {"name": "foir_post_loan", "value": 0.58, "threshold": 0.50}},
    {"code": "MODERATE_BUREAU_SCORE","type": "NEGATIVE", "impact": "MEDIUM",
     "message": "CIB score 648 is in the moderate band (600-680)",
     "metric": {"name": "bureau_score", "value": 648, "threshold": 680}},
    {"code": "HEALTHY_DSCR",         "type": "POSITIVE", "impact": "HIGH",
     "message": "Vehicle generates 1.52x the proposed EMI in net contribution",
     "metric": {"name": "dscr", "value": 1.52, "threshold": 1.30}},
    {"code": "STRONG_DOWN_PAYMENT",  "type": "POSITIVE", "impact": "MEDIUM",
     "message": "Down payment of 35% is well above the 20% minimum",
     "metric": {"name": "down_payment_ratio", "value": 0.35, "threshold": 0.20}}
  ]
}
```

Ordering: NEGATIVE before POSITIVE, then by impact HIGH → MEDIUM → LOW. Officers read the top of the
list first, and the top of the list is what blocks approval.

### 7.5.7 Worked example — full application

**Setup**

| Item | Value |
|------|-------|
| Applicant | Ram Bahadur Tamang, OWNER_DRIVER, 34 |
| Route | Kathmandu–Dhulikhel, score **90.12**, Class A |
| Vehicle | BYD e6 taxi — on-road NPR 4,600,000; 71.7 kWh; real range 380 km; battery warranty 8 years |
| Requested | NPR 3,200,000 over 60 months @ 13.0%; down payment NPR 1,400,000 (30.4%) |
| Income | salary 0 + business revenue NPR 145,000 + other 0 = **145,000** |
| Expenses | household 42,000 + business 18,000 = 60,000 |
| Existing | 1 loan, EMI NPR 12,400, outstanding NPR 385,000 |
| Bureau | score 742, 54 months history, 0 defaults, max DPD 12, 1 enquiry, not blacklisted |
| Experience | 9 years driving, 6 commercial, no prior EV |
| Operation | 240 km/day, 26 operating days, 8 trips/day @ NPR 950 |

**Knock-outs:** none fire (down payment 30.4% ≥ 20%, score 742 ≥ 550, age 34 → 39 at maturity,
amount 3.2M ≤ 5M, tenure 60 ≤ 84, route Class A, licence valid).

**Vehicle economics**

```
energy_per_km      = 71.7 / 380                    = 0.188684 kWh/km
energy_cost_per_km = 0.188684 × 12 × 1.08          = 2.445347 NPR/km
daily_energy       = 240 × 2.445347                = NPR 586.88
daily_maintenance  = 240 × 0.55                    = NPR 132.00
daily_driver_cost  = 0 (owner-driver)
daily_fixed        = (85,000 + 12,000) / (12 × 26) = NPR 310.90
daily_gross        = 8 × 950                       = NPR 7,600.00
daily_net          = 7600 − 586.88 − 132 − 0 − 310.90 = NPR 6,570.22
seasonal_factor    = 1 − 6/365                     = 0.983562
monthly_net        = 6570.22 × 26 × 0.983562       = NPR 168,017.61
```

**Provisional EMI** on the requested structure (P = 3,200,000; r = 13/1200 = 0.0108333; n = 60):

```
(1+r)^60 = 1.9088...
EMI = 3,200,000 × 0.0108333 × 1.9088 / 0.9088 = NPR 72,809.83
```

```
DSCR           = 168,017.61 / 72,809.83 = 2.3076
FOIR post-loan = (12,400 + 72,809.83) / 145,000 = 0.587654
DTI pre-loan   = 12,400 / 145,000 = 0.085517
disposable     = 145,000 − 60,000 − 12,400 = NPR 72,600
disp / EMI     = 72,600 / 72,809.83 = 0.99712
payback_months = (4,600,000 − 1,400,000) / 168,017.61 = 19.05
warranty ratio = 96 months / 60 months = 1.60
```

**Customer components**

| Component | Sub-factor | Value | Sub-score | Weight | Contribution |
|-----------|-----------|-------|-----------|--------|--------------|
| **Credit history** | bureau 742 | 742 | 79.28 | 0.50 | 39.640 |
| | max DPD 12 | 12 | 84.00 | 0.20 | 16.800 |
| | defaults 0 | 0 | 100.00 | 0.15 | 15.000 |
| | history 54m | 54 | 96.25 | 0.10 | 9.625 |
| | enquiries 1 | 1 | 95.00 | 0.05 | 4.750 |
| | **= 85.82** | | | | |
| **Income & cash flow** | income 145k | 145,000 | 95.60 | 0.35 | 33.460 |
| | disp/EMI 0.99712 | 0.99712 | 24.64 | 0.35 | 8.624 |
| | proof: bank stmt verified | — | 90.00 | 0.20 | 18.000 |
| | balance/EMI 1.1400 | 1.1400 | 43.50 | 0.10 | 4.350 |
| | **= 64.43** | | | | |
| **Existing debt** | FOIR 0.587654 | 0.587654 | 30.59 | 0.50 | 15.293 |
| | DTI 0.085517 | 0.085517 | 91.45 | 0.25 | 22.862 |
| | 1 obligation | 1 | 90.00 | 0.15 | 13.500 |
| | 0 overdue | 0 | 100.00 | 0.10 | 10.000 |
| | **= 61.66** | | | | |
| **Down payment** | ratio 0.304348 | 0.304348 | 68.74 | 1.00 | 68.740 |
| **Experience** | commercial 6y | 6 | 84.00 | 0.50 | 42.000 |
| | driving 9y | 9 | 86.00 | 0.25 | 21.500 |
| | no prior EV | false | 50.00 | 0.15 | 7.500 |
| | licence 30m | 30 | 100.00 | 0.10 | 10.000 |
| | **= 81.00** | | | | |
| **Vehicle economics** | DSCR 2.3076 | 2.3076 | 94.46 | 0.45 | 42.507 |
| | energy 2.4453/km | 2.4453 | 76.09 | 0.20 | 15.219 |
| | payback 19.05m | 19.05 | 91.19 | 0.20 | 18.239 |
| | warranty 1.60 | 1.60 | 100.00 | 0.15 | 15.000 |
| | **= 90.96** | | | | |

```
Customer Score = 85.82×0.30 + 64.43×0.25 + 61.66×0.15 + 68.74×0.10 + 81.00×0.10 + 90.96×0.10
               = 25.746 + 16.108 + 9.249 + 6.874 + 8.100 + 9.096
               = 75.173  →  quantised 75.17  →  Grade B (Good)
```

**Final risk score**

```
Final = 90.12×0.40 + 75.17×0.40 + 90.96×0.20
      = 36.048 + 30.068 + 18.192
      = 84.308  →  quantised 84.31  →  Grade B (Low risk)
```

**Decision matrix:** row 3 (hard reject) does not apply — score 84.31 ≥ 50, grade B is not E, and
DSCR 2.31 ≥ 1.00. Row 2 (auto-approve) requires `foir_post_loan ≤ 0.50`; actual is **0.587654**, so
the single failed gate is `FOIR_ABOVE_APPROVAL_THRESHOLD`. ⇒ **MANUAL REVIEW**.

**Structuring**

```
max_ltv (grade B)  = 75%
amount_by_ltv      = 4,600,000 × 0.75 = 3,450,000
emi_by_foir        = 0.55 × 145,000 − 12,400 = 67,350.00
emi_by_dscr        = 168,017.61 / 1.25 = 134,414.09
emi_capacity       = min(67,350.00; 134,414.09) = 67,350.00      ← FOIR binds
amount_by_emi      = principal_from_emi(67,350.00, 13%, 60) = 2,960,415.44
recommended_amount = min(3,200,000; 3,450,000; 2,960,415.44; 5,000,000) = 2,960,415.44
                     → rounded down to the nearest 10,000 = NPR 2,960,000
recommended_tenure = min(60, 72, 96+12) = 60 months
recommended_rate   = 12.50 + 0.50 = 13.00%
recomputed EMI     = calculate_emi(2,960,000, 13%, 60) = NPR 67,349.10
DSCR at that EMI   = 168,017.61 / 67,349.10 = 2.4947
FOIR at that EMI   = (12,400 + 67,349.10) / 145,000 = 0.5500
applied LTV        = 2,960,000 / 4,600,000 = 64.35%
binding constraint = EMI_CAPACITY_FOIR
```

**Output shown to the officer**

> **Recommendation: MANUAL REVIEW** — Final risk score **84.31 / 100** (Grade B, Low risk)
> **Reasons**
> - ⚠ Post-loan FOIR of 58.77% at the requested amount exceeds the 50% approval threshold (HIGH)
> - ⚠ Disposable income covers the requested EMI only 1.00× (HIGH)
> - ✓ Vehicle generates 2.31× the requested EMI in net contribution — very strong DSCR (HIGH)
> - ✓ Route Kathmandu–Dhulikhel is Class A with adequate charging (HIGH)
> - ✓ Clean bureau record: score 742, no defaults, 54 months of history (MEDIUM)
> - ✓ Down payment of 30.43% is above the 20% minimum (MEDIUM)
>
> **Suggested structure:** reduce the loan to **NPR 2,960,000** over **60 months @ 13.00%** →
> EMI **NPR 67,349**, FOIR **55.00%**, DSCR **2.49**, LTV **64.35%**.
> *Note: at NPR 2,960,000 the file still misses the 50% FOIR approval gate; either take additional
> income evidence, add a co-applicant, or approve at Risk Manager discretion with justification.*

This is exactly the intended behaviour: a strong asset and a strong route do **not** silently
override a stretched borrower — but the officer is shown precisely what would fix it.

---

## 7.6 Reference implementation (Python)

```python
# backend/app/engines/normalizer.py
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

Q2 = Decimal("0.01")

def q(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)

def clamp(value: Decimal, lo: Decimal = Decimal(0), hi: Decimal = Decimal(100)) -> Decimal:
    return max(lo, min(hi, value))

def normalize(rule: dict, inputs: dict[str, Any]) -> Decimal:
    """Convert one raw input into a 0-100 sub-score. Pure."""
    kind = rule["type"]

    if kind == "BANDED":
        value = inputs.get(rule["input"])
        for band in rule["bands"]:
            if band["value"] == value:
                return clamp(Decimal(str(band["score"])))
        return clamp(Decimal(str(rule.get("default_score", 50))))

    if kind == "LINEAR":
        raw = inputs.get(rule["input"])
        if raw is None:
            return clamp(Decimal(str(rule.get("default_score", 50))))
        v = Decimal(str(raw))
        pts = [(Decimal(str(p["x"])), Decimal(str(p["y"]))) for p in rule["points"]]
        if v <= pts[0][0]:
            return clamp(pts[0][1])
        if v >= pts[-1][0]:
            return clamp(pts[-1][1])
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= v < x1:
                return clamp(y0 + (y1 - y0) * (v - x0) / (x1 - x0))
        return clamp(pts[-1][1])

    if kind == "COMPOSITE":
        total = Decimal(0)
        for sub in rule["sub_factors"]:
            total += Decimal(str(sub["weight"])) * normalize(sub["rule"], inputs)
        return clamp(total)

    raise ValueError(f"Unknown scoring rule type: {kind}")
```

```python
# backend/app/engines/route_engine.py  (abridged)
class RouteScoringEngine(ScoringEngine):
    engine_version = "route-engine@1.0.0"

    def score(self, payload: RouteScoringInput, config: ScoringConfig) -> ScoreResult:
        inputs = self._derive(payload)          # density, ratios, margins - pure arithmetic
        components: list[ComponentResult] = []
        total = Decimal(0)

        for comp in config.active_components():          # ordered, weights already validated
            normalized = normalize(comp.scoring_rules, inputs)
            normalized = self._apply_caps(comp.code, normalized, inputs)
            weighted = normalized * comp.weight
            total += weighted
            components.append(ComponentResult(
                code=comp.code, label=comp.label,
                raw_inputs=self._inputs_for(comp.code, inputs),
                normalized_score=q(normalized), weight=comp.weight,
                weighted_score=q(weighted),
                explanation=self._explain(comp.code, inputs, normalized),
                factors=[],
            ))

        total = q(clamp(total))
        risks, positives = evaluate_factors(ROUTE_FACTOR_RULES, inputs)
        return ScoreResult(
            total_score=total,
            grade=config.grade_for(total),
            components=components,
            risk_factors=risks,
            positive_factors=positives,
            reason_codes=[f.code for f in risks + positives],
            config_version_id=config.id,
            engine_version=self.engine_version,
        )
```

**Invariant tests that must pass for every engine (property-based, hypothesis):**

```python
def test_weighted_sum_equals_total(any_valid_route_input, active_config):
    r = RouteScoringEngine().score(any_valid_route_input, active_config)
    assert abs(sum(c.weighted_score for c in r.components) - r.total_score) <= Decimal("0.01")

def test_score_is_bounded(any_valid_route_input, active_config):
    r = RouteScoringEngine().score(any_valid_route_input, active_config)
    assert Decimal(0) <= r.total_score <= Decimal(100)

def test_determinism(any_valid_route_input, active_config):
    e = RouteScoringEngine()
    assert e.score(any_valid_route_input, active_config) == e.score(any_valid_route_input, active_config)

def test_monotonic_in_charging_density(base_input, active_config):
    better = base_input.model_copy(update={"charging_station_count": base_input.charging_station_count + 3})
    e = RouteScoringEngine()
    assert e.score(better, active_config).total_score >= e.score(base_input, active_config).total_score
```

---

## 7.7 Calibration and governance

| Activity | Frequency | Owner | Output |
|----------|-----------|-------|--------|
| Score distribution review (are we bunching in one grade?) | Monthly | Risk Manager | Adjust curve points, not weights |
| Override-rate review | Monthly | Risk Manager | High override rate ⇒ the model disagrees with practice; investigate which component |
| Alert precision review | Monthly | Portfolio Manager | Tune rule thresholds |
| Component-vs-outcome analysis | Quarterly | Risk Manager + Analytics | Which components separate good from bad? |
| Full recalibration (logistic fit) | Once ≥ 300 loans reach 12 months on book | Analytics | Replace expert weights with fitted coefficients; keep the same engine and interface |
| Configuration change control | Per change | Risk Manager | Draft → preview against last 50 applications → publish → audit entry |

**Preview-before-publish (Phase 2, US-G6):** a draft configuration can be run over the last N
decided applications to produce a migration matrix (how many files change grade or decision) before
it is activated. This is the single most valuable safeguard against a well-intentioned weight change
silently loosening credit standards.
