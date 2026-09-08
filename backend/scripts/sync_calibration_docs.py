"""Propagate the ROUTE v2 calibration (Class A boundary 80 -> 88) into the documentation.

This is a risk-policy change: grade bands only. Component weights and curves are byte
identical between v1 and v2, which the test suite asserts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows consoles default to cp1252; these scripts print mathematical symbols.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SCORES = json.loads((Path(__file__).parent / "demo_route_scores.json").read_text(encoding="utf-8"))

EDITS: dict[str, list[tuple[str, str]]] = {
    # ---------------------------------------------------------------- Doc 07
    "docs/07-SCORING-ENGINE.md": [
        ("""### 7.3.7 Route classification (default thresholds)

| Grade | Range | Label | Risk level | Recommendation |
|-------|-------|-------|-----------|----------------|
| **A** | 80–100 | Class A — Low Risk | LOW | `ELIGIBLE` — eligible for EV financing at standard terms |
| **B** | 60–79.99 | Class B — Medium Risk | MEDIUM | `ELIGIBLE_WITH_CONDITIONS` — eligible with tighter LTV/tenure |
| **C** | 0–59.99 | Class C — High Risk | HIGH | `NOT_ELIGIBLE` — not eligible without a Risk Manager waiver |

Stored in `scoring_configurations.grade_thresholds`; fully editable, and additional grades may be
added without a code change.""",
         """### 7.3.7 Route classification

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
added without a code change."""),
        ("Route Score = 91.13×0.30 + 88.25×0.20 + 87.85×0.30 + 98.67×0.10 + 89.05×0.10\n"
         "            = 27.339 + 17.650 + 26.355 +  9.867 +  8.905\n"
         "            = 90.116  →  quantised 90.12  →  Grade A (Class A — Low Risk)",
         "Route Score = 91.13×0.30 + 88.25×0.20 + 87.85×0.30 + 98.67×0.10 + 89.05×0.10\n"
         "            = 27.339 + 17.650 + 26.355 +  9.867 +  8.905\n"
         "            = 90.116  →  quantised 90.12  →  Grade A (≥ 88 under ROUTE v2)"),
    ],
    # ---------------------------------------------------------------- Doc 01
    "docs/01-PRD.md": [
        ("| **A** | 80–100 | Class A — Low Risk | LOW | `ELIGIBLE` |",
         "| **A** | 88–100 | Class A — Low Risk | LOW | `ELIGIBLE` |"),
        ("| **B** | 60–79.99 | Class B — Medium Risk | MEDIUM | `ELIGIBLE_WITH_CONDITIONS` |",
         "| **B** | 60–87.99 | Class B — Medium Risk | MEDIUM | `ELIGIBLE_WITH_CONDITIONS` |"),
        ("| Route Score | 0–100 | Class A ≥ 80, B 60–79, C < 60 |",
         "| Route Score | 0–100 | Class A ≥ 88, B 60–87.99, C < 60 (ROUTE v2) |"),
        ("**Route Score** = Charging 30% + Road 20% + Demand 30% + Revenue 10% + Risk 10%",
         "**Route Score** = Charging 30% + Road 20% + Demand 30% + Revenue 10% + Risk 10%\n"
         "(Class A ≥ 88 under the active ROUTE configuration v2; see [07-SCORING-ENGINE.md](07-SCORING-ENGINE.md) §7.3.7)"),
        ("| FR-2.5 | Classify the route into a configurable band (default Class A ≥ 80, B 60–79, C < 60). | [M] |",
         "| FR-2.5 | Classify the route into a configurable band (ROUTE v2: Class A ≥ 88, B 60–87.99, C < 60). | [M] |"),
    ],
    # ---------------------------------------------------------------- Doc 00
    "docs/00-EXECUTIVE-SUMMARY.md": [
        ("| **1. Route Assessment** | Is this operating corridor financially and operationally viable for an EV? | Route Score 0–100, Class A/B/C, charging adequacy verdict, revenue potential, explicit risk and positive factor lists |",
         "| **1. Route Assessment** | Is this operating corridor financially and operationally viable for an EV? | Route Score 0–100, Class A/B/C (A ≥ 88 under the active configuration), charging adequacy verdict, revenue potential, explicit risk and positive factor lists |"),
    ],
    # ---------------------------------------------------------------- Doc 03
    "docs/03-UIUX-DESIGN.md": [
        ("| Low / Good | `success-600` on `success-50` | ● shield-check | pill | \"Low Risk\" / \"Class A\" / \"Grade A\" | Route A, grades A–B, GREEN loans, on-time |",
         "| Low / Good | `success-600` on `success-50` | ● shield-check | pill | \"Low Risk\" / \"Class A\" / \"Grade A\" | Route A (≥ 88), grades A–B, GREEN loans, on-time |"),
    ],
}


def band_summary() -> str:
    grades = [s["grade"] for s in SCORES.values()]
    return f"{grades.count('A')} Class A / {grades.count('B')} Class B / {grades.count('C')} Class C"


DOC16_OLD_NOTE = """> **Calibration note (7 Class A / 2 Class B / 1 Class C):**
> the default expert weights are generous — most realistic valley and highway corridors clear
> the 80-point Class A threshold. Before go-live the Risk Manager should either raise the
> Class A boundary or steepen the demand and revenue curves, so that the grade actually
> discriminates across the book. This is exactly the review Doc 07 §7.7 prescribes, and it is a
> configuration change, not a code change."""


def doc16_new_note() -> str:
    return f"""> **Calibration applied — ROUTE configuration v2 ({band_summary()}).**
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
> no code deploy."""


def apply(rel: str, pairs: list[tuple[str, str]]) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    hits = 0
    missing = []
    for old, new in pairs:
        if old in text:
            text = text.replace(old, new)
            hits += 1
        else:
            missing.append(old.splitlines()[0][:60])
    path.write_text(text, encoding="utf-8")
    note = f"  {rel}: {hits}/{len(pairs)}"
    if missing:
        note += "  (not found: " + "; ".join(missing) + ")"
    print(note)


def main() -> None:
    print("propagating ROUTE v2 calibration:")
    for rel, pairs in EDITS.items():
        apply(rel, pairs)

    doc16 = ROOT / "docs" / "16-SAMPLE-DATA.md"
    text = doc16.read_text(encoding="utf-8")
    if DOC16_OLD_NOTE in text:
        text = text.replace(DOC16_OLD_NOTE, doc16_new_note())
        doc16.write_text(text, encoding="utf-8")
        print("  docs/16-SAMPLE-DATA.md: calibration note replaced")
    else:
        print("  docs/16-SAMPLE-DATA.md: calibration note NOT found")

    print(f"\ndistribution under ROUTE v2: {band_summary()}")


if __name__ == "__main__":
    main()
