"""Rewrite the Doc 07 worked examples with engine-verified arithmetic.

The curve tables and weights in Doc 07 are normative; the summary figures in the worked
examples were originally computed by hand and contained interpolation slips. The engine
implements the curves exactly, so the engine's output is authoritative and the prose is
corrected to match it. Every number written here is asserted by
tests/unit/engines/, so the two cannot drift again.
"""

from __future__ import annotations

from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "07-SCORING-ENGINE.md"

REPLACEMENTS: list[tuple[str, str]] = [
    # ---------------------------------------------------------------- §7.3.2
    (
        """An `INADEQUATE` verdict adds a `CRITICAL` risk factor and caps the component score at 25 regardless
of the curve output — a configurable cap (`charging.inadequate_cap`, default 25).""",
        """An `INADEQUATE` verdict adds a `CRITICAL` risk factor and caps the component score at 25 regardless
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
> ⇒ INADEQUATE`. This is a configuration and rule change, not an engine change.""",
    ),
    # ---------------------------------------------------------------- §7.3.9 reference vehicle
    (
        "| battery / range (reference) | 24 kWh / 140 km |",
        "| battery / range (reference vehicle) | 24 kWh / 140 km |",
    ),
    # ---------------------------------------------------------------- §7.3.9 derived block
    (
        """```
density              = 6 / (30/10)          = 2.00 stations per 10 km
gap_to_range_ratio   = 12 / 140             = 0.0857
fast_charger_share   = 3 / 6                = 0.50
daily_km             = 8 × 30               = 240 km
terrain_factor       = ROLLING              = 0.08
energy_cost_per_km   = (24/140) × 12 × 1.08 = 2.222 NPR/km
est_daily_energy     = 240 × 2.222          = NPR 533.28
est_daily_revenue    = 8 × 950              = NPR 7,600
daily_margin         = 7600 − 1800 − 533.28 = NPR 5,266.72
margin_ratio         = 5266.72 / 7600       = 0.693
revenue_per_km       = 7600 / 240           = NPR 31.67
demand_index         = 1200 + 0             = 1200
```""",
        """```
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
```""",
    ),
    # ---------------------------------------------------------------- §7.3.9 component table
    (
        """| **Charging** | density | 2.00 | 90.0 | 0.40 | 36.00 |
| | gap/range | 0.0857 | 96.9 | 0.35 | 33.91 |
| | fast share | 0.50 | 85.0 | 0.25 | 21.25 |
| | **= 91.16** | | | | |""",
        """| **Charging** | density | 2.00 | 90.00 | 0.40 | 36.000 |
| | gap/range | 0.085714 | 96.79 | 0.35 | 33.875 |
| | fast share | 0.50 | 85.00 | 0.25 | 21.250 |
| | **= 91.13** | | | | |""",
    ),
    (
        """| **Revenue** | daily margin | 5,266.72 | 100.0 | 0.50 | 50.00 |
| | margin ratio | 0.693 | 100.0 | 0.30 | 30.00 |
| | revenue/km | 31.67 | 93.4 | 0.20 | 18.68 |
| | **= 98.68** | | | | |""",
        """| **Revenue** | daily margin | 5,266.79 | 100.00 | 0.50 | 50.000 |
| | margin ratio | 0.6930 | 100.00 | 0.30 | 30.000 |
| | revenue/km | 31.6667 | 93.33 | 0.20 | 18.667 |
| | **= 98.67** | | | | |""",
    ),
    (
        """| **Risk** | flood LOW | — | 85.0 | 0.35 | 29.75 |
| | monsoon 6 days | — | 91.6 | 0.30 | 27.48 |
| | seasonal LOW | — | 85.0 | 0.20 | 17.00 |
| | security LOW | — | 100.0 | 0.15 | 15.00 |
| | **= 89.23** | | | | |""",
        """| **Risk** | flood LOW | — | 85.00 | 0.35 | 29.750 |
| | monsoon 6 days | 6 | 91.00 | 0.30 | 27.300 |
| | seasonal LOW | — | 85.00 | 0.20 | 17.000 |
| | security LOW | — | 100.00 | 0.15 | 15.000 |
| | **= 89.05** | | | | |""",
    ),
    # ---------------------------------------------------------------- §7.3.9 total
    (
        """```
Route Score = 91.16×0.30 + 88.25×0.20 + 87.85×0.30 + 98.68×0.10 + 89.23×0.10
            = 27.348 + 17.650 + 26.355 +  9.868 +  8.923
            = 90.14  →  Grade A (Class A — Low Risk)
```""",
        """```
Route Score = 91.13×0.30 + 88.25×0.20 + 87.85×0.30 + 98.67×0.10 + 89.05×0.10
            = 27.339 + 17.650 + 26.355 +  9.867 +  8.905
            = 90.116  →  quantised 90.12  →  Grade A (Class A — Low Risk)
```

Component scores are quantised to two decimal places *before* weighting, because that is how they
are persisted (`route_score_components.normalized_score` is `NUMERIC(5,2)`). This makes the stored
breakdown and the stored total reconcile exactly rather than approximately.""",
    ),
    # ---------------------------------------------------------------- §7.3.9 factors
    (
        """**Generated factors**
Positive: `DENSE_CHARGING`, `FAST_CHARGING_AVAILABLE`, `EXCELLENT_ROAD`, `STRONG_DEMAND`,
`HEALTHY_MARGIN`, `SHORT_ROUTE_EV_FRIENDLY`, `NO_SEASONAL_DISRUPTION`.
Risk: none above MEDIUM.""",
        """**Generated factors**
Positive (6): `DENSE_CHARGING`, `FAST_CHARGING_AVAILABLE`, `EXCELLENT_ROAD`, `STRONG_DEMAND`,
`HEALTHY_MARGIN`, `SHORT_ROUTE_EV_FRIENDLY`.
Risk: none.

`NO_SEASONAL_DISRUPTION` does **not** fire: its threshold is ≤ 5 disruption days and this corridor
has 6.""",
    ),
    # ---------------------------------------------------------------- §7.5.7 economics
    (
        """```
energy_per_km      = 71.7 / 380              = 0.18868 kWh/km
energy_cost_per_km = 0.18868 × 12 × 1.08     = 2.4453 NPR/km
daily_energy       = 240 × 2.4453            = NPR 586.87
daily_maintenance  = 240 × 0.55              = NPR 132.00
daily_driver_cost  = 0 (owner-driver)
daily_fixed        = (85,000 + 12,000) / (12 × 26) = NPR 310.90
daily_gross        = 8 × 950                 = NPR 7,600.00
daily_net          = 7600 − 586.87 − 132 − 0 − 310.90 = NPR 6,570.23
seasonal_factor    = 1 − 6/365               = 0.98356
monthly_net        = 6570.23 × 26 × 0.98356  = NPR 168,043
```""",
        """```
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
```""",
    ),
    (
        """```
(1+r)^60 = 1.908857
EMI = 3,200,000 × 0.0108333 × 1.908857 / 0.908857 = NPR 72,802
```""",
        """```
(1+r)^60 = 1.9088...
EMI = 3,200,000 × 0.0108333 × 1.9088 / 0.9088 = NPR 72,809.83
```""",
    ),
    (
        """```
DSCR           = 168,043 / 72,802 = 2.308
FOIR post-loan = (12,400 + 72,802) / 145,000 = 0.5876
DTI pre-loan   = 12,400 / 145,000 = 0.0855
disposable     = 145,000 − 60,000 − 12,400 = NPR 72,600
disp / EMI     = 72,600 / 72,802 = 0.997
payback_months = (4,600,000 − 1,400,000) / 168,043 = 19.0
warranty ratio = 96 months / 60 months = 1.60
```""",
        """```
DSCR           = 168,017.61 / 72,809.83 = 2.3076
FOIR post-loan = (12,400 + 72,809.83) / 145,000 = 0.587654
DTI pre-loan   = 12,400 / 145,000 = 0.085517
disposable     = 145,000 − 60,000 − 12,400 = NPR 72,600
disp / EMI     = 72,600 / 72,809.83 = 0.99712
payback_months = (4,600,000 − 1,400,000) / 168,017.61 = 19.05
warranty ratio = 96 months / 60 months = 1.60
```""",
    ),
]

# §7.5.7 customer component table — replaced wholesale
OLD_CUSTOMER_TABLE_START = "| **Credit history** | bureau 742 | | 79.4 | 0.50 | 39.70 |"
NEW_CUSTOMER_TABLE = """| **Credit history** | bureau 742 | 742 | 79.28 | 0.50 | 39.640 |
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
| | **= 90.96** | | | | |"""


def replace_customer_table(text: str) -> str:
    start = text.index(OLD_CUSTOMER_TABLE_START)
    end_marker = "\n\n```\nCustomer Score ="
    end = text.index(end_marker, start)
    return text[:start] + NEW_CUSTOMER_TABLE + text[end:]


TAIL_REPLACEMENTS: list[tuple[str, str]] = [
    (
        """```
Customer Score = 85.88×0.30 + 65.21×0.25 + 58.73×0.15 + 68.70×0.10 + 81.40×0.10 + 92.13×0.10
               = 25.764 + 16.303 + 8.810 + 6.870 + 8.140 + 9.213
               = 75.10  →  Grade B (Good)
```""",
        """```
Customer Score = 85.82×0.30 + 64.43×0.25 + 61.66×0.15 + 68.74×0.10 + 81.00×0.10 + 90.96×0.10
               = 25.746 + 16.108 + 9.249 + 6.874 + 8.100 + 9.096
               = 75.173  →  quantised 75.17  →  Grade B (Good)
```""",
    ),
    (
        """```
Final = 90.14×0.40 + 75.10×0.40 + 92.13×0.20
      = 36.056 + 30.040 + 18.426
      = 84.52  →  Grade B (Low risk)
```""",
        """```
Final = 90.12×0.40 + 75.17×0.40 + 90.96×0.20
      = 36.048 + 30.068 + 18.192
      = 84.308  →  quantised 84.31  →  Grade B (Low risk)
```""",
    ),
    (
        """**Decision matrix:** row 2 requires `foir_post_loan ≤ 0.50`; actual is **0.5876**. Row 2 fails.
Row 3 does not apply (score 84.52 ≥ 50, grade B, DSCR 2.31 ≥ 1.00). ⇒ **MANUAL REVIEW**.""",
        """**Decision matrix:** row 3 (hard reject) does not apply — score 84.31 ≥ 50, grade B is not E, and
DSCR 2.31 ≥ 1.00. Row 2 (auto-approve) requires `foir_post_loan ≤ 0.50`; actual is **0.587654**, so
the single failed gate is `FOIR_ABOVE_APPROVAL_THRESHOLD`. ⇒ **MANUAL REVIEW**.""",
    ),
    (
        """```
max_ltv (grade B)  = 75%
amount_by_ltv      = 4,600,000 × 0.75 = 3,450,000
emi_by_foir        = 0.55 × 145,000 − 12,400 = 67,350
emi_by_dscr        = 168,043 / 1.25 = 134,434
emi_capacity       = min(67,350, 134,434) = 67,350
amount_by_emi      = 67,350 × (1.908857 − 1) / (0.0108333 × 1.908857) = 2,960,412
recommended_amount = min(3,200,000; 3,450,000; 2,960,412; 5,000,000) = NPR 2,960,412
                     → rounded down to NPR 2,960,000
recommended_tenure = min(60, 72, 96+12) = 60 months
recommended_rate   = 12.50 + 0.50 = 13.00%
recomputed EMI     = 2,960,000 × 0.0108333 × 1.908857 / 0.908857 = NPR 67,341
DSCR at that EMI   = 168,043 / 67,341 = 2.495
FOIR at that EMI   = (12,400 + 67,341) / 145,000 = 0.550
applied LTV        = 2,960,000 / 4,600,000 = 64.3%
```""",
        """```
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
```""",
    ),
    (
        """> **Recommendation: MANUAL REVIEW** — Final risk score **84.52 / 100** (Grade B, Low risk)""",
        """> **Recommendation: MANUAL REVIEW** — Final risk score **84.31 / 100** (Grade B, Low risk)""",
    ),
    (
        """> - ⚠ Post-loan FOIR of 58.8% at the requested amount exceeds the 50% approval threshold (HIGH)
> - ⚠ Disposable income barely covers the requested EMI (coverage 1.00×) (HIGH)
> - ✓ Vehicle generates 2.31× the requested EMI in net contribution — very strong DSCR (HIGH)
> - ✓ Route Kathmandu–Dhulikhel is Class A with adequate charging (HIGH)
> - ✓ Clean bureau record: score 742, no defaults, 54 months of history (MEDIUM)
> - ✓ Down payment of 30.4% is above the 20% minimum (MEDIUM)
>
> **Suggested structure:** reduce the loan to **NPR 2,960,000** over **60 months @ 13.00%** →
> EMI **NPR 67,341**, FOIR **55.0%**, DSCR **2.50**, LTV **64.3%**.""",
        """> - ⚠ Post-loan FOIR of 58.77% at the requested amount exceeds the 50% approval threshold (HIGH)
> - ⚠ Disposable income covers the requested EMI only 1.00× (HIGH)
> - ✓ Vehicle generates 2.31× the requested EMI in net contribution — very strong DSCR (HIGH)
> - ✓ Route Kathmandu–Dhulikhel is Class A with adequate charging (HIGH)
> - ✓ Clean bureau record: score 742, no defaults, 54 months of history (MEDIUM)
> - ✓ Down payment of 30.43% is above the 20% minimum (MEDIUM)
>
> **Suggested structure:** reduce the loan to **NPR 2,960,000** over **60 months @ 13.00%** →
> EMI **NPR 67,349**, FOIR **55.00%**, DSCR **2.49**, LTV **64.35%**.""",
    ),
    (
        "**Route Score** = Charging 30% + Road 20% + Demand 30% + Revenue 10% + Risk 10%",
        "**Route Score** = Charging 30% + Road 20% + Demand 30% + Revenue 10% + Risk 10%",
    ),
]

BANNER = """
> **Verification status.** Every figure in the worked examples below is produced by the
> implementation in `backend/app/engines/` and asserted, sub-factor by sub-factor, in
> `backend/tests/unit/engines/`. The curve tables and weights are normative; the summary figures
> are derived from them. An earlier draft of this document carried hand-computed totals that were
> slightly off (route 90.14 vs 90.12, customer 75.10 vs 75.17, vehicle economics 92.13 vs 90.96,
> final 84.52 vs 84.31) — the curves were right and the arithmetic was not. The values here are the
> engine's, and the test suite fails if this document and the code ever disagree again.
"""


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    misses = []
    for old, new in REPLACEMENTS:
        if old not in text:
            misses.append(old.splitlines()[0][:70])
            continue
        text = text.replace(old, new)

    if OLD_CUSTOMER_TABLE_START in text:
        text = replace_customer_table(text)
    else:
        misses.append("customer component table")

    for old, new in TAIL_REPLACEMENTS:
        if old not in text:
            misses.append(old.splitlines()[0][:70])
            continue
        text = text.replace(old, new)

    anchor = "## 7.1 Engine principles"
    if BANNER.strip() not in text:
        text = text.replace(anchor, BANNER.strip() + "\n\n---\n\n" + anchor, 1)

    DOC.write_text(text, encoding="utf-8")
    print(f"synced {DOC.name}")
    for m in misses:
        print(f"  !! pattern not found: {m}")


if __name__ == "__main__":
    main()
