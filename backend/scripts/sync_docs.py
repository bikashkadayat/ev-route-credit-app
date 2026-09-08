"""Rewrite the documentation's route table and demo seed from engine-verified output.

Run after any scorecard change:

    PYTHONPATH=. python scripts/compute_demo_routes.py
    PYTHONPATH=. python scripts/sync_docs.py

Rationale: the worked examples in the documentation are executable test assertions
(Doc 10 §10.2.1). Hand-maintained score tables drift the moment a curve moves, and a
drifted table is worse than no table because it looks authoritative. This script makes
the engine the single source of truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCORES = json.loads(
    (Path(__file__).parent / "demo_route_scores.json").read_text(encoding="utf-8")
)

ORDER = [
    "RT-KTM-DHU-001", "RT-BUT-BHW-006", "RT-KTM-RING-002", "RT-BKT-BAN-004",
    "RT-ITA-DHR-010", "RT-LAL-CHA-003", "RT-BRT-BHD-005", "RT-NPJ-KOH-008",
    "RT-PKR-LEK-007", "RT-KTM-JIR-009",
]

# Static route attributes for the documentation table (the scored inputs)
ATTRS = {
    "RT-KTM-DHU-001": ("Kathmandu–Dhulikhel", "30.0", "INTERCITY", "PITCH / GOOD / ROLLING", "6 (3)", "12.0", "8.0", "950", "HIGH", "MODERATE", "6"),
    "RT-BUT-BHW-006": ("Butwal–Bhairahawa", "24.0", "HIGHWAY", "PITCH / EXCELLENT / FLAT", "5 (2)", "10.0", "8.0", "300 + freight", "HIGH", "MODERATE", "10"),
    "RT-KTM-RING-002": ("Kathmandu Ring Road circuit", "27.5", "URBAN", "PITCH / GOOD / FLAT", "9 (4)", "6.0", "12.0", "420", "SEVERE", "SATURATED", "3"),
    "RT-BKT-BAN-004": ("Bhaktapur–Banepa", "18.0", "SUBURBAN", "PITCH / GOOD / ROLLING", "4 (2)", "9.0", "9.0", "380", "HIGH", "HIGH", "8"),
    "RT-ITA-DHR-010": ("Itahari–Dharan", "16.0", "HIGHWAY", "PITCH / GOOD / FLAT", "4 (2)", "8.0", "10.0", "260", "HIGH", "HIGH", "9"),
    "RT-LAL-CHA-003": ("Lalitpur–Chapagaun", "12.0", "SUBURBAN", "PITCH / FAIR / ROLLING", "3 (1)", "7.0", "10.0", "260", "HIGH", "HIGH", "4"),
    "RT-BRT-BHD-005": ("Birtamod–Bhadrapur", "22.0", "INTERCITY", "PITCH / GOOD / FLAT", "3 (1)", "14.0", "7.0", "320", "MODERATE", "MODERATE", "12"),
    "RT-NPJ-KOH-008": ("Nepalgunj–Kohalpur", "15.0", "SUBURBAN", "PITCH / FAIR / FLAT", "2 (1)", "11.0", "9.0", "240", "MODERATE", "HIGH", "14 (flood HIGH)"),
    "RT-PKR-LEK-007": ("Pokhara–Lekhnath", "16.0", "SUBURBAN", "MIXED 70% / FAIR / HILLY", "2 (0)", "16.0", "6.0", "280", "MODERATE", "MODERATE", "22 (landslide MODERATE)"),
    "RT-KTM-JIR-009": ("Kathmandu–Jiri", "187.0", "RURAL", "MIXED 55% / POOR / STEEP", "1 (0)", "96.0", "1.5", "1,400", "LOW", "LOW", "45 (landslide HIGH)"),
}


def build_route_table() -> str:
    lines = [
        "| Code | Route | km | Type | Road / condition / gradient | Stations (fast) | Max gap | "
        "Trips/day | Fare | Traffic | Competition | Monsoon days | **Score** | **Class** |",
        "|------|-------|----|------|-----------------------------|-----------------|---------|"
        "-----------|------|---------|-------------|--------------|-----------|-----------|",
    ]
    for code in ORDER:
        a = ATTRS[code]
        s = SCORES[code]
        lines.append(
            f"| {code} | {a[0]} | {a[1]} | {a[2]} | {a[3]} | {a[4]} | {a[5]} | {a[6]} | "
            f"{a[7]} | {a[8]} | {a[9]} | {a[10]} | **{s['score']}** | **{s['grade']}** |"
        )
    return "\n".join(lines)


def replace_block(text: str, start_marker: str, end_marker: str, new_block: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + new_block + "\n\n" + text[end:]


def sync_doc_16() -> None:
    path = ROOT / "docs" / "16-SAMPLE-DATA.md"
    text = path.read_text(encoding="utf-8")
    table = build_route_table()
    text = replace_block(
        text,
        "| Code | Route | km | Type |",
        "**Why these ten:**",
        table,
    )
    grades = [SCORES[c]["grade"] for c in ORDER]
    counts = {g: grades.count(g) for g in ("A", "B", "C")}
    text = text.replace(
        "**Why these ten:** they span every grade, every road type, both passenger and freight demand,\n"
        "flood-exposed and landslide-exposed corridors, a saturated urban circuit and one deliberately\n"
        "unfinanceable long rural route. `RT-KTM-JIR-009` exists so the demo can show a knock-out.",
        f"**Why these ten:** they span every grade, every road type, both passenger and freight demand,\n"
        f"flood-exposed and landslide-exposed corridors, a saturated urban circuit and one deliberately\n"
        f"unfinanceable long rural route. `RT-KTM-JIR-009` exists so the demo can show a knock-out.\n\n"
        f"> **These scores are generated, not hand-written.** They come from\n"
        f"> `backend/scripts/compute_demo_routes.py` running `route-engine@1.0.0` against the shipped\n"
        f"> ROUTE configuration v1, and are regenerated whenever the scorecard changes.\n"
        f">\n"
        f"> **Calibration note ({counts['A']} Class A / {counts['B']} Class B / {counts['C']} Class C):**\n"
        f"> the default expert weights are generous — most realistic valley and highway corridors clear\n"
        f"> the 80-point Class A threshold. Before go-live the Risk Manager should either raise the\n"
        f"> Class A boundary or steepen the demand and revenue curves, so that the grade actually\n"
        f"> discriminates across the book. This is exactly the review Doc 07 §7.7 prescribes, and it is a\n"
        f"> configuration change, not a code change.",
    )
    path.write_text(text, encoding="utf-8")
    print(f"synced {path.relative_to(ROOT)}")


def sync_doc_16_assessment_refs() -> None:
    """Point-fix the individual score mentions elsewhere in doc 16."""
    path = ROOT / "docs" / "16-SAMPLE-DATA.md"
    text = path.read_text(encoding="utf-8")
    for old, new in [
        ("| LA-2026-000101 | #1 Ram Bahadur Tamang | BYD e6 | KTM-DHU | 3,200,000 | 60 | 1,400,000 | 90.1 | 75.1 | **84.5** | MANUAL_REVIEW | PENDING_DECISION |",
         "| LA-2026-000101 | #1 Ram Bahadur Tamang | BYD e6 | KTM-DHU | 3,200,000 | 60 | 1,400,000 | 90.12 | 75.17 | **84.31** | MANUAL_REVIEW | PENDING_DECISION |"),
    ]:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    sync_doc_16()
    sync_doc_16_assessment_refs()
    print("done")


if __name__ == "__main__":
    sys.exit(main())
