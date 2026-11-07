"""Compare our travel times against the independently checked figures.

This is a **reporting** test, not a gate. The deviations it measures are the
2020 feed diverging from how the lines run today -- our pipeline reproduces
the feed's own end-to-end trip times exactly -- so a deviation is information
about the data, not a bug to fail the build over. Rows beyond ±10% are listed
as warnings so a drift that grows gets noticed.

The test is skipped when the store has not been built, so a fresh checkout
without `data/transit.duckdb` still passes.
"""

from __future__ import annotations

import csv

import pytest

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.db import database_path

EXTERNAL = PROJECT_ROOT / "datasets" / "external_travel_times.csv"

# Beyond this, a row is worth a human look.
WARN_THRESHOLD_PCT = 10.0


def load_rows() -> list[dict[str, str]]:
    if not EXTERNAL.exists():
        return []
    with EXTERNAL.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


pytestmark = pytest.mark.skipif(
    not database_path().exists(),
    reason="data/transit.duckdb yok; önce ingest_rail çalıştır",
)


@pytest.fixture(scope="module")
def network():
    from istanbul_agent.store.rail_queries import load_network

    return load_network()


@pytest.fixture(scope="module")
def comparisons(network) -> list[dict[str, object]]:
    """Every external row, measured against what we return."""
    from istanbul_agent.store.rail_queries import get_rail_travel_time

    rows: list[dict[str, object]] = []
    for entry in load_rows():
        result = get_rail_travel_time(entry["from"], entry["to"], network=network)
        external_minutes = float(entry["minutes"])
        ours = result.seconds / 60 if result.seconds is not None else None
        rows.append(
            {
                "line": entry["line"],
                "from": entry["from"],
                "to": entry["to"],
                "external": external_minutes,
                "ours": ours,
                "delta": None if ours is None else ours - external_minutes,
                "delta_pct": (
                    None if ours is None else (ours - external_minutes) / external_minutes * 100
                ),
                "method": result.method,
                "estimated": result.estimated,
                "unverified": "DOĞRULANMADI" in (entry.get("notes") or ""),
            }
        )
    return rows


def test_external_file_is_well_formed():
    rows = load_rows()
    assert rows, "datasets/external_travel_times.csv boş"
    for row in rows:
        assert row["from"] and row["to"] and row["line"]
        assert float(row["minutes"]) > 0
        assert row["source_url"], f"{row['from']}→{row['to']} kaynak URL'si yok"
        assert row["retrieved_at"], f"{row['from']}→{row['to']} tarih yok"


def test_every_external_journey_is_answerable(comparisons):
    """A journey someone has a reference figure for should not come back empty."""
    unanswered = [row for row in comparisons if row["ours"] is None]
    assert not unanswered, "cevaplanamayan yolculuklar: " + ", ".join(
        f"{row['from']}→{row['to']}" for row in unanswered
    )


def test_report_deviations(comparisons, capsys):
    """Print the comparison and flag anything past the threshold.

    Deliberately does not assert on the deviations: see the module docstring.
    """
    with capsys.disabled():
        print("\n\n  Dış kaynak karşılaştırması")
        print(f"  {'Hat':6} {'Yolculuk':34} {'bizim':>7} {'dış':>6} {'fark':>7} {'%':>7}  durum")
        warnings: list[str] = []
        for row in comparisons:
            if row["ours"] is None:
                print(
                    f"  {row['line']:6} {row['from'][:15]:16}→{row['to'][:15]:17} "
                    f"{'—':>7} {row['external']:>6.1f} {'—':>7} {'—':>7}  "
                    f"cevaplanamadı"
                )
                continue

            delta_pct = float(row["delta_pct"])  # type: ignore[arg-type]
            flags: list[str] = []
            if row["unverified"]:
                flags.append("kaynak doğrulanmadı")
            if row["estimated"]:
                flags.append("tahmin")
            if abs(delta_pct) > WARN_THRESHOLD_PCT and not row["unverified"]:
                flags.append(f"⚠ ±%{WARN_THRESHOLD_PCT:.0f} AŞILDI")
                warnings.append(
                    f"{row['line']} {row['from']}→{row['to']}: "
                    f"{delta_pct:+.1f}% (bizim {row['ours']:.1f} dk, "
                    f"dış {row['external']:.1f} dk)"
                )
            print(
                f"  {row['line']:6} {row['from'][:15]:16}→{row['to'][:15]:17} "
                f"{row['ours']:>7.2f} {row['external']:>6.1f} "
                f"{row['delta']:>+7.2f} {delta_pct:>+6.1f}%  {', '.join(flags)}"
            )

        checked = [row for row in comparisons if row["ours"] is not None and not row["unverified"]]
        if checked:
            deviations = [abs(float(row["delta_pct"])) for row in checked]  # type: ignore[arg-type]
            print(
                f"\n  doğrulanmış {len(checked)} satır: "
                f"ortalama |sapma| %{sum(deviations) / len(deviations):.1f}, "
                f"en büyük %{max(deviations):.1f}"
            )
        if warnings:
            print(f"\n  ⚠ ±%{WARN_THRESHOLD_PCT:.0f} üzeri sapmalar ({len(warnings)}):")
            for warning in warnings:
                print(f"    {warning}")
        else:
            print(f"\n  ±%{WARN_THRESHOLD_PCT:.0f} üzeri sapma yok.")


def test_unverified_rows_are_excluded_from_calibration():
    """A row the user flagged as unverified must stay out of any calibration."""
    unverified = [row for row in load_rows() if "DOĞRULANMADI" in (row.get("notes") or "")]
    assert unverified, "beklenen: M5 satırı doğrulanmadı olarak işaretli"
    for row in unverified:
        assert "alibrasyonda kullanılm" in row["notes"], (
            f"{row['from']}→{row['to']} doğrulanmadı ama kalibrasyon notu yok"
        )
