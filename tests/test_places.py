"""Yer arama: dört adım, kümeleme, ilgili adlar, çevre araması.

Testler elle kurulan küçük bir `transit_places` tablosu kullanıyor. Tabloda
gerçek verideki üç zorluk küçük ölçekte var:

- aynı adı taşıyan ve **birbirine yakın** noktalar (bir kavşağın iki yönü),
- aynı adı taşıyan ve **uzaktaki** noktalar (iki ayrı semtte "D-100"),
- işletmeci ekiyle kayıtlı iskele adları ("Kadıköy ŞH.").
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.db import connect
from istanbul_agent.store.ingest import PLACES_SCHEMA
from istanbul_agent.store.places import (
    CLUSTER_RADIUS_M,
    find_nearby_places,
    find_places,
    load_places,
)

# (place_id, mode, name, code, detail, lat, lon, source, data_as_of, is_stale)
ROWS = [
    (
        "rayli:kadikoy",
        "metro",
        "Kadıköy",
        "M4",
        None,
        40.9907,
        29.0220,
        "Metro API",
        "2026-11-08",
        False,
    ),
    (
        "bus:1",
        "otobus",
        "KADIKÖY",
        "401031",
        "direction: A",
        40.9933,
        29.0243,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    # Aynı ad, 40 m ötede: tek yer sayılmalı.
    (
        "bus:2",
        "otobus",
        "KADIKÖY",
        "402031",
        "direction: B",
        40.9936,
        29.0244,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    # Aynı ad, şehrin öteki ucunda: ayrı yer sayılmalı.
    (
        "bus:3",
        "otobus",
        "KADIKÖY",
        "202951",
        None,
        41.1106,
        29.9053,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "vapur:1",
        "vapur",
        "Kadıköy ŞH.",
        None,
        None,
        40.9918,
        29.0235,
        "Deniz iskeleleri",
        "2026-11-04",
        False,
    ),
    (
        "vapur:2",
        "vapur",
        "Üsküdar Turyol",
        None,
        None,
        41.0259,
        29.0141,
        "Deniz iskeleleri",
        "2026-11-04",
        False,
    ),
    (
        "vapur:3",
        "vapur",
        "Üsküdar ŞH.",
        None,
        None,
        41.0262,
        29.0145,
        "Deniz iskeleleri",
        "2026-11-04",
        False,
    ),
    (
        "bus:ort0",
        "otobus",
        "ORTAKÖY A",
        None,
        None,
        41.0470,
        29.0260,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:ort1",
        "otobus",
        "ORTAKÖY B",
        None,
        None,
        41.0471,
        29.0261,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:ort2",
        "otobus",
        "ORTAKÖY C",
        None,
        None,
        41.0472,
        29.0262,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:ort3",
        "otobus",
        "ORTAKÖY D",
        None,
        None,
        41.0473,
        29.0263,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:ort4",
        "otobus",
        "ORTAKÖY E",
        None,
        None,
        41.0474,
        29.0264,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:ort5",
        "otobus",
        "ORTAKÖY F",
        None,
        None,
        41.0475,
        29.0265,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "bus:4",
        "otobus",
        "FISTIK AĞACI",
        None,
        None,
        41.0300,
        29.0200,
        "İETT GTFS",
        "2026-04-21",
        False,
    ),
    (
        "rayli:eski",
        "tramvay",
        "Eski İstasyon",
        "T9",
        None,
        41.0000,
        28.9000,
        "GTFS 308",
        "2020-07-09",
        True,
    ),
]


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "places.duckdb"
    connection = connect(database)
    try:
        connection.execute(PLACES_SCHEMA)
        from istanbul_agent.store.station_names import collapsed, normalize, token_key

        connection.executemany(
            "INSERT INTO transit_places VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    place_id,
                    mode,
                    name,
                    normalize(name),
                    collapsed(name),
                    token_key(name),
                    code,
                    detail,
                    lat,
                    lon,
                    source,
                    data_as_of,
                    is_stale,
                )
                for place_id, mode, name, code, detail, lat, lon, source, data_as_of, is_stale in ROWS
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return database


@pytest.fixture
def places(store):
    return load_places(store)


class TestMatching:
    def test_exact_name_wins(self, places):
        search = find_places("Kadıköy", places=places)
        assert search.how == "tam ad"
        assert search.found

    def test_user_spelling_without_turkish_letters(self, places):
        assert find_places("kadikoy", places=places).found
        assert find_places("KADIKOY", places=places).found

    def test_facility_suffix_is_ignored_by_the_shared_normaliser(self, places):
        """ "iskele", "durağı" gibi ekler yer adı değil, tesis adı (rule 5)."""
        search = find_places("kadıköy iskelesi", places=places)
        assert search.found
        assert search.how == "tam ad"

    def test_word_order_difference(self, places):
        search = find_places("ağacı fıstık", places=places)
        assert search.found
        assert search.how == "kelime sırası"

    def test_unknown_name_returns_candidates_not_emptiness(self, places):
        search = find_places("Paris", places=places)
        assert not search.found
        assert search.candidates

    def test_empty_query_is_not_a_match(self, places):
        assert not find_places("   ", places=places).found


class TestClustering:
    def test_nearby_points_with_the_same_name_become_one_place(self, places):
        search = find_places("Kadıköy", places=places, modes=("otobus",))
        kadikoy = [cluster for cluster in search.clusters if cluster.name == "KADIKÖY"]
        # İki yakın durak tek yer, uzaktaki ayrı yer: iki küme.
        assert len(kadikoy) == 2
        joined = next(cluster for cluster in kadikoy if cluster.point_count == 2)
        assert sorted(joined.codes) == ["401031", "402031"]

    def test_distant_points_with_the_same_name_stay_separate(self, places):
        search = find_places("Kadıköy", places=places, modes=("otobus",))
        far = next(cluster for cluster in search.clusters if cluster.lon > 29.5)
        assert far.point_count == 1

    def test_cluster_radius_is_the_documented_one(self):
        assert CLUSTER_RADIUS_M == 300.0

    def test_rail_is_offered_before_bus(self, places):
        """Kullanıcı "Kadıköy" derken otobüs bayrağını kastetmiyor."""
        search = find_places("Kadıköy", places=places)
        assert search.clusters[0].mode == "metro"

    def test_staleness_survives_clustering(self, places):
        search = find_places("Eski İstasyon", places=places)
        assert search.clusters[0].is_stale is True


class TestRelatedNames:
    def test_operator_suffixed_piers_are_offered_as_related(self, places):
        """İskeleler işletmeci ekiyle kayıtlı; ad tam tutmasa da görünmeli."""
        search = find_places("Kadıköy", places=places)
        assert "Kadıköy ŞH." in search.related

    def test_related_names_exclude_the_matched_name(self, places):
        search = find_places("Kadıköy", places=places)
        assert "Kadıköy" not in search.related
        assert "KADIKÖY" not in search.related

    def test_a_few_prefix_matches_are_all_returned(self, places):
        """İki iskele de "Üsküdar" ile başlıyor ve ikisi de Üsküdar iskelesi.

        Reddetmek çıkmaz sokaktı: model "böyle bir iskele yok" diye
        cevaplıyordu. Birkaç adaya uyan ön ek artık hepsini döndürüyor.
        """
        search = find_places("Üsküdar", places=places)
        assert search.found
        assert search.how == "ön ek (2 aday)"
        assert {cluster.name for cluster in search.clusters} == {
            "Üsküdar ŞH.",
            "Üsküdar Turyol",
        }

    def test_a_prefix_matching_too_many_names_is_still_refused(self, places):
        """Altı ayrı ada uyan ön ekte seçim kullanıcının."""
        search = find_places("Ortaköy", places=places)
        assert not search.found
        assert len(search.related) >= 5


class TestPrefix:
    def test_a_prefix_leading_to_one_name_matches(self, places):
        search = find_places("fıstık ağ", places=places)
        assert search.found
        assert search.how == "ön ek"
        assert search.clusters[0].name == "FISTIK AĞACI"


class TestNearby:
    def test_orders_by_distance_and_reports_it(self, places):
        found = find_nearby_places(40.9907, 29.0220, radius_m=600, places=places)
        distances = [cluster.distance_m for cluster in found]
        assert distances == sorted(distances)
        assert found[0].name == "Kadıköy"
        assert found[0].distance_m == 0.0

    def test_radius_excludes_the_far_side_of_the_city(self, places):
        found = find_nearby_places(40.9907, 29.0220, radius_m=600, places=places)
        assert all(cluster.lon < 29.5 for cluster in found)

    def test_mode_filter(self, places):
        found = find_nearby_places(40.9907, 29.0220, radius_m=2000, modes=("vapur",), places=places)
        assert [cluster.mode for cluster in found] == ["vapur"]

    def test_limit_is_respected(self, places):
        found = find_nearby_places(40.9907, 29.0220, radius_m=5000, limit=2, places=places)
        assert len(found) == 2

    def test_nothing_within_radius_is_an_empty_list_not_an_error(self, places):
        assert find_nearby_places(39.0, 32.0, radius_m=500, places=places) == []


class TestModeResolution:
    """Mod adı: eşanlamlılar kabul edilir, tanınmayan mod hata verir.

    Ortak `normalize()` tam bu kelimeleri tesis eki sayıp attığı için
    (station_names rule 5) burada ayrı bir eşleme var.
    """

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("iskele", "vapur"),
            ("vapur", "vapur"),
            ("feribot", "vapur"),
            ("otobüs", "otobus"),
            ("otobus", "otobus"),
            ("bus", "otobus"),
            ("füniküler", "funikuler"),
            ("minibüs", "minibus"),
            ("dolmuş", "dolmus"),
            ("METRO", "metro"),
            ("  Tram  ", "tramvay"),
        ],
    )
    def test_accepted_spellings(self, written, expected):
        from istanbul_agent.store.places import resolve_mode

        modes, error = resolve_mode(written)
        assert (modes, error) == ((expected,), None)

    def test_no_mode_is_not_an_error(self):
        from istanbul_agent.store.places import resolve_mode

        assert resolve_mode(None) == ((), None)
        assert resolve_mode("  ") == ((), None)

    def test_an_unknown_mode_names_the_valid_ones(self):
        from istanbul_agent.store.places import resolve_mode

        modes, error = resolve_mode("uçak")
        assert modes == ()
        assert error and "otobus" in error and "vapur" in error

    def test_every_synonym_resolves_to_a_mode_the_table_really_has(self):
        """Bir eşanlamlı saklanmayan bir moda açılırsa arama sessizce boşalır.

        `rayli` kendine, `tren`/`marmaray` da `tren`e eşleniyordu; tabloda
        o iki değerde tek satır yok, yani bu modlarla yapılan **her** arama
        "yer bulunamadı" dönüyordu (karar 0015). Eski test bunu yakalamıyordu
        çünkü eşanlamlının kendi adını doğruluyordu, tabloyu değil.
        """
        from istanbul_agent.store.places import MODE_SYNONYMS, STORED_MODES

        dead = {
            written: modes
            for written, modes in MODE_SYNONYMS.items()
            if not set(modes) <= STORED_MODES
        }
        assert dead == {}

    def test_labels_and_priority_cover_exactly_the_stored_modes(self):
        from istanbul_agent.store.places import MODE_LABELS, MODE_PRIORITY, STORED_MODES

        assert set(MODE_LABELS) == STORED_MODES
        assert set(MODE_PRIORITY) == STORED_MODES

    def test_the_error_message_vocabulary_is_accepted_input(self):
        """Hata mesajı ne yazıyorsa araç onu kabul etmek zorunda."""
        from istanbul_agent.store.places import MODE_VOCABULARY, resolve_mode

        for word in MODE_VOCABULARY:
            modes, error = resolve_mode(word)
            assert error is None, word
            assert modes, word

    def test_rail_is_an_umbrella_over_the_four_rail_modes(self):
        from istanbul_agent.store.places import resolve_mode

        modes, error = resolve_mode("raylı")
        assert error is None
        assert set(modes) == {"metro", "tramvay", "funikuler", "teleferik"}

    def test_marmaray_looks_at_metro_rows(self):
        """Marmaray ayrı bir mod değil: istasyonları `metro` satırı."""
        from istanbul_agent.store.places import resolve_mode

        for written in ("marmaray", "tren", "banliyö"):
            assert resolve_mode(written) == (("metro",), None), written


class TestSimilarityTies:
    def test_equal_scores_are_not_broken_arbitrarily(self, places):
        """Altı "ORTAKÖY X" adının hepsi aynı puanı alıyor.

        Birini seçmek, kullanıcının sormadığı durağı seçmek olurdu; yayılım
        sınırı aşıldığı için araç seçim yapmayıp adayları döndürür.
        """
        search = find_places("Ortaköy", places=places)
        assert not search.found
        assert len(search.related) >= 5

    def test_a_clear_single_near_match_still_matches(self, places):
        search = find_places("Fıstık Ağacı", places=places)
        assert search.found
        assert search.clusters[0].name == "FISTIK AĞACI"
