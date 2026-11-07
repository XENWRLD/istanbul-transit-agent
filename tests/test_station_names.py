"""Station name normalisation, driven by the real mismatches found in Faz 1.

Every pair here comes from comparing the 2020 GTFS feed against the live
Metro İstanbul station list; the cases are the ones that actually occurred,
not invented ones.
"""

import pytest

from istanbul_agent.store.station_names import collapsed, normalize, similarity, variants


class TestTurkishCasefold:
    """Rule 2: `I` and `İ` must both fold to `i`."""

    def test_api_shouting_matches_feed_mixed_case(self):
        # The API says YENIKAPI, the feed says Yenikapı.
        assert normalize("YENIKAPI") == normalize("Yenikapı")

    def test_dotted_and_dotless_i_agree(self):
        assert normalize("İSTANBUL") == normalize("istanbul") == "istanbul"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Şişli", "sisli"),
            ("Göztepe", "goztepe"),
            ("Üsküdar", "uskudar"),
            ("Çekmeköy", "cekmekoy"),
            ("Bağcılar", "bagcilar"),
        ],
    )
    def test_turkish_letters_fold_to_ascii(self, raw, expected):
        assert normalize(raw) == expected


class TestAbbreviations:
    """Rule 4: the feed shortens what the API spells out."""

    @pytest.mark.parametrize(
        ("api", "gtfs"),
        [
            ("Çırçır Mahallesi", "Çırçır Mah."),
            ("Karadeniz Mahallesi", "Karadeniz Mah."),
            ("Göztepe Mahallesi", "Göztepe Mah."),
            ("Ziya Gökalp Mahallesi", "Ziya Gökalp Mah."),
            ("Merter Tekstil Merkezi", "Merter Teks. Mrk."),
        ],
    )
    def test_expanded_abbreviation_matches(self, api, gtfs):
        assert normalize(api) == normalize(gtfs)

    def test_camii_and_cami_match(self):
        assert normalize("İskele Camii") == normalize("İskele Cami")


class TestWordBoundaries:
    """The two sources disagree about where a word ends."""

    @pytest.mark.parametrize(
        ("api", "gtfs"),
        [
            ("Fıstıkağacı", "Fıstık Ağacı"),
            ("Ayşekadın", "Ayşe Kadın"),
            ("Yavuzselim", "Yavuz Selim"),
            ("Piyer Loti", "Piyerloti"),
        ],
    )
    def test_collapsed_key_matches_across_spacing(self, api, gtfs):
        assert collapsed(api) == collapsed(gtfs)

    def test_strict_key_does_not_match_across_spacing(self):
        # Deliberate: normalize() stays strict so the looser key is a separate
        # opt-in, not a silent widening of every lookup.
        assert normalize("Fıstıkağacı") != normalize("Fıstık Ağacı")


class TestSuffixes:
    """Rule 5: mode and facility suffixes are not part of the place name."""

    @pytest.mark.parametrize(
        "suffixed",
        [
            "Kadıköy İskelesi",
            "Kadıköy iskele",
            "Kadıköy Metro",
            "Kadıköy İstasyonu",
            "Kadıköy Tramvay",
            "Kadıköy Aktarma",
        ],
    )
    def test_suffix_is_dropped(self, suffixed):
        assert normalize(suffixed) == "kadikoy"

    def test_trailing_platform_number_is_dropped(self):
        assert normalize("Mecidiyeköy 2") == normalize("Mecidiyeköy")

    def test_a_name_made_only_of_suffixes_survives(self):
        # Must not normalise to "", which would collide with every blank.
        assert normalize("Metro") != ""
        assert normalize("İskele") != ""


class TestSquares:
    """Rule 6: `Meydan` is kept by default -- it distinguishes stations."""

    def test_square_is_distinct_by_default(self):
        # Real case: M1B/T4 have both "Bağcılar" and "Bağcılar Meydan".
        assert normalize("Bağcılar Meydan") != normalize("Bağcılar")

    def test_square_can_be_folded_on_request(self):
        assert normalize("Bağcılar Meydan", drop_square=True) == normalize("Bağcılar")

    def test_meydani_and_meydan_agree(self):
        assert normalize("Özgürlük Meydanı") == normalize("Özgürlük Meydan")


class TestEdgeCases:
    def test_blank_input(self):
        assert normalize(None) == ""
        assert normalize("") == ""
        assert normalize("   ") == ""

    def test_punctuation_becomes_space(self):
        assert normalize("Mescid-i Selam") == normalize("Mescid i Selam")

    def test_distinct_stations_do_not_collide(self):
        """The real risk of over-normalising: two real stations folding into one."""
        names = [
            "Yenikapı",
            "Yenibosna",
            "Yenidoğan",
            "Bakırköy",
            "Bakırköy Sahil",
            "Sancaktepe",
            "Sancaktepe Şehir Hastanesi",
            "Alibeyköy Merkez",
            "Alibeyköy Cep Otogarı",
            "Eyüpsultan Teleferik",
            "Eyüpsultan Devlet Hastanesi",
        ]
        keys = [normalize(n) for n in names]
        assert len(set(keys)) == len(names), "iki ayrı istasyon aynı anahtara düştü"


class TestSimilarity:
    def test_identical_after_normalisation_scores_one(self):
        assert similarity("YENIKAPI", "Yenikapı") == 1.0

    def test_unrelated_names_score_low(self):
        assert similarity("Kadıköy", "Başakşehir") < 0.5

    def test_blank_scores_zero(self):
        assert similarity(None, "Kadıköy") == 0.0

    def test_variants_returns_three_keys(self):
        assert len(variants("Bağcılar Meydan")) == 3
