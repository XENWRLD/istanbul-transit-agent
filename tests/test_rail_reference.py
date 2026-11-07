"""Parsing the rail reference spreadsheets, on fixtures built from real rows.

Every row shape here was taken from datasets 312 and 313 as published, defects
included: the `#N/A` cells, the en-dash-plus-non-breaking-space separator in
the M7 block, the implausible lengths, and the line codes glued to station
names in the transfer table.
"""

import openpyxl
import pytest

from istanbul_agent.store.rail_reference import (
    PLAUSIBLE_MAX_M,
    PLAUSIBLE_MIN_M,
    load_rail_reference,
)


def write_sheet(path, rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


@pytest.fixture
def reference_dir(tmp_path):
    """Spreadsheets mirroring the published ones, defects and all."""
    write_sheet(
        tmp_path / "hat_uzunluklari.xlsx",
        [
            ("Hat", "İstasyonlar Arası", "Uzunluk (metre)"),
            # Ordinary rows.
            ("F1", "Kabatas-Taksım", 511),
            ("M2", "Sışhane-Halıc", 632),
            # Decimal comma, as some rows have.
            ("M12", "60.Yıl Parkı-Tütüncü Mehmet Efendi", "1137,2"),
            # The M7 block: en-dash plus a non-breaking space.
            ("M7", "Mahmutbey – Bölge Parkı", 1010),
            ("M7", "Bölge Parkı\xa0– Atatürk Mah.", 1010),
            # Excel error value.
            ("TF1", "Macka- Taskısla", "#N/A"),
            # Implausible: 929 km between neighbouring stations.
            ("M9", "Masko-Baharıye", 929542),
            # Implausible: 49 m between two metro stations.
            ("M1A", "Ulubatlı-Emnıyet", 49),
            # Blank line code.
            (None, "Bir-Iki", 500),
        ],
    )
    write_sheet(
        tmp_path / "aktarma_bilgileri.xlsx",
        [
            ("Hat Adı", "Aktarma Bilgileri (Nereden)", "Aktarma Bilgileri (Nereye)"),
            ("F1", "F1 Kabataş", "T1 Kabataş"),
            ("M1", "M1A Zeytinburnu", "T1 Zeytinburnu"),
            # Code glued to the name, no space.
            ("M1", "M1Yenikapı", "Marmaray Yenikapı"),
            # Target is not rail.
            ("M1", "M1A Merter", "Merter Metrobüs"),
            ("M2", "M2 Şişli-Mecidiyeköy", "M7"),
        ],
    )
    return tmp_path


class TestSegmentLengths:
    def test_ordinary_rows_are_read(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Kabataş", "Taksim") == 511
        assert reference.segment_metres("Şişhane", "Haliç") == 632

    def test_lookup_ignores_direction(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Taksim", "Kabataş") == 511

    def test_lookup_tolerates_spelling(self, reference_dir):
        """312 writes 'Taksım' with a dotless i; callers write 'Taksim'."""
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("TAKSIM", "KABATAS") == 511

    def test_en_dash_and_nbsp_separator_is_handled(self, reference_dir):
        """11 real M7 rows would be dropped by a plain hyphen split."""
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Bölge Parkı", "Atatürk Mahallesi") == 1010
        assert reference.segment_metres("Mahmutbey", "Bölge Parkı") == 1010

    def test_decimal_comma_is_parsed(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("60.Yıl Parkı", "Tütüncü Mehmet Efendi") == pytest.approx(
            1137.2
        )


class TestExclusions:
    def test_excel_error_value_excluded(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Maçka", "Taşkışla") is None
        assert any("#N/A" in str(row["reason"]) for row in reference.excluded)

    def test_implausibly_long_excluded(self, reference_dir):
        """M9's 929 km row looks like a factor-of-1000 slip; we do not guess."""
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Masko", "Bahariye") is None

    def test_implausibly_short_excluded(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Ulubatlı", "Emniyet") is None

    def test_blank_line_code_excluded(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert reference.segment_metres("Bir", "Iki") is None

    def test_every_exclusion_is_recorded_with_a_reason(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert len(reference.excluded) == 4
        for row in reference.excluded:
            assert row["reason"]
            assert row["source"] == "312"

    def test_plausibility_bounds_are_sane(self):
        assert PLAUSIBLE_MIN_M < 200 < PLAUSIBLE_MAX_M
        assert PLAUSIBLE_MAX_M < 50_000


class TestTransfers:
    def test_line_code_split_from_station_name(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        kabatas = next(t for t in reference.transfers if t.from_station == "Kabataş")
        assert kabatas.from_line == "F1"
        assert kabatas.to_line == "T1"
        assert kabatas.to_station == "Kabataş"

    def test_code_glued_to_name_is_split(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        yenikapi = next(
            t for t in reference.transfers if t.from_line == "M1" and "Yenikapı" in t.from_station
        )
        assert yenikapi.from_station == "Yenikapı"

    def test_non_rail_target_is_flagged(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        merter = next(t for t in reference.transfers if t.from_station == "Merter")
        assert merter.to_is_rail is False
        assert merter not in reference.rail_transfers()

    def test_rail_transfers_exclude_metrobus(self, reference_dir):
        reference = load_rail_reference(reference_dir)
        assert all(t.to_is_rail for t in reference.rail_transfers())
        assert len(reference.rail_transfers()) == 4

    def test_target_without_station_name_keeps_the_line(self, reference_dir):
        """'M7' alone as a target still records the line."""
        reference = load_rail_reference(reference_dir)
        mecidiyekoy = next(
            t for t in reference.transfers if t.from_station == "Şişli-Mecidiyeköy"
        )
        assert mecidiyekoy.to_line == "M7"


class TestMissingFiles:
    def test_absent_files_give_an_empty_reference(self, tmp_path):
        reference = load_rail_reference(tmp_path)
        assert reference.segments == []
        assert reference.transfers == []
