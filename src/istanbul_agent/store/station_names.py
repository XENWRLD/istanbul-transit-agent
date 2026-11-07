"""Normalising station and stop names so two sources can be matched.

The GTFS feed and the Metro İstanbul API spell the same station differently:
the API shouts (`YENIKAPI`), the feed is mixed case and Turkish-accented
(`Yenikapı`), and either side may append or omit a suffix (`Metro`,
`İstasyonu`, `Meydanı`, `-1`). Normalisation has to fold all of that away
while keeping the names distinct enough not to collide.

The rules, in order:

1. **Unicode fold.** NFKC, then strip combining marks.
2. **Turkish casefold.** Lowercase, but map Turkish letters to their ASCII
   counterparts *before* lowercasing, so `I` and `İ` both land on `i`. Doing
   it the other way round turns `I` into `ı` and breaks `YENIKAPI` ↔
   `Yenikapı`, which is why this is explicit rather than `str.lower()`.
3. **Punctuation to spaces.** Hyphens, dots, slashes, apostrophes, brackets.
   `D-100` keeps its digits but loses the dash, so `D100`.
4. **Expand abbreviations.** The GTFS feed shortens words the API spells out:
   `Mah.` → `mahallesi`, `Teks. Mrk.` → `tekstil merkezi`, `Ü.` →
   `universitesi`, `Cad.` → `caddesi`, `Hast.` → `hastanesi`.
5. **Drop operational suffixes** (`metro`, `istasyonu`, `iskelesi`, `durağı`,
   `tramvay`, `funikuler`, `aktarma`, `peron`...). These mark the mode or the
   facility, never which place it is. A trailing bare number (`-1`, `2`) is
   also dropped: those are platform or entrance numbers.
6. **Keep `meydan`/`meydani`.** NOT dropped by default -- "Bağcılar Meydan"
   and "Bağcılar" are different stations on M1B/T4. Pass
   ``drop_square=True`` to fold them for a second, looser matching pass.
7. **Collapse whitespace.**

`normalize()` is the key for exact lookups. The two sources also disagree
about where a word ends (`Fıstıkağacı` vs `Fıstık Ağacı`, `Yavuzselim` vs
`Yavuz Selim`), so `collapsed()` additionally removes all spaces -- a second
key that catches those without loosening the first. `similarity()` scores a
fuzzy pair so near-misses can be surfaced for human review rather than
silently matched.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

__all__ = ["collapsed", "normalize", "similarity", "variants"]

# Turkish letters mapped before lowercasing (see rule 2).
_TURKISH_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
        "Â": "a",
        "â": "a",
        "Î": "i",
        "î": "i",
        "Û": "u",
        "û": "u",
    }
)

# Suffix tokens that name a mode or facility, not a place.
_SUFFIX_TOKENS = frozenset(
    {
        "metro",
        "metrosu",
        "istasyon",
        "istasyonu",
        "ist",
        "stn",
        "station",
        "iskele",
        "iskelesi",
        "ih",
        "vapur",
        "vapuru",
        "durak",
        "duragi",
        "duraklari",
        "peron",
        "peronlar",
        "peronlari",
        "tramvay",
        "tramvayi",
        "t",
        "funikuler",
        "funiküler",
        "teleferik",
        "aktarma",
        "aktarmasi",
        "transfer",
        "marmaray",
        "tcdd",
        "peronu",
        "peronlarl",
        "yonu",
        "yon",
    }
)

# Folded away only when drop_square=True (see rule 5).
_SQUARE_TOKENS = frozenset({"meydan", "meydani"})

# GTFS abbreviations, expanded to whatever the API spells out (see rule 4).
# Applied token by token after punctuation has already become whitespace.
_ABBREVIATIONS = {
    "mah": "mahallesi",
    "mh": "mahallesi",
    "cad": "caddesi",
    "cd": "caddesi",
    "sok": "sokak",
    "teks": "tekstil",
    "mrk": "merkezi",
    "merkez": "merkezi",
    "u": "universitesi",
    "uni": "universitesi",
    "univ": "universitesi",
    "universite": "universitesi",
    "hast": "hastanesi",
    "hastane": "hastanesi",
    "camii": "cami",
    "lisesi": "lise",
    "mehmet": "mehmet",
    "bul": "bulvari",
    "blv": "bulvari",
    # Kept as a station-distinguishing token (rule 6), but the two sources
    # inflect it differently: "Özgürlük Meydanı" vs "Özgürlük Meydan".
    "meydani": "meydan",
    "havalimani": "havalimani",
    "hv": "havalimani",
}

_PUNCT_RE = re.compile(r"[-_.,/\\'’`\"()\[\]{}:;!?*&+]")
_TRAILING_NUMBER_RE = re.compile(r"\s+\d{1,2}$")
_SPACE_RE = re.compile(r"\s+")


def _fold(name: str) -> str:
    text = unicodedata.normalize("NFKC", name)
    text = text.translate(_TURKISH_FOLD)
    text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return text.lower()


def normalize(name: str | None, *, drop_square: bool = False) -> str:
    """Return the comparison key for a station or stop name.

    Args:
        name: Raw name from either source. ``None`` and blanks give ``""``.
        drop_square: Also fold away `Meydan`/`Meydanı`. Off by default
            because it merges genuinely distinct stations; use it only for a
            second, looser matching pass whose hits a human reviews.
    """
    if not name:
        return ""

    text = _fold(name)
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    text = _TRAILING_NUMBER_RE.sub("", text)

    tokens = [_ABBREVIATIONS.get(token, token) for token in text.split()]

    drop = _SUFFIX_TOKENS | _SQUARE_TOKENS if drop_square else _SUFFIX_TOKENS
    kept = [token for token in tokens if token not in drop]
    # Never normalise a name out of existence: if every token was a suffix,
    # keep them so "Metro" does not become "".
    return " ".join(kept or tokens)


def collapsed(name: str | None, *, drop_square: bool = False) -> str:
    """`normalize()` with every space removed.

    A second lookup key for the case where the two sources disagree about word
    boundaries: `Fıstıkağacı` vs `Fıstık Ağacı`, `Piyerloti` vs `Piyer Loti`.
    """
    return normalize(name, drop_square=drop_square).replace(" ", "")


def variants(name: str | None) -> tuple[str, str, str]:
    """The three lookup keys for `name`, from strictest to loosest."""
    return normalize(name), collapsed(name), collapsed(name, drop_square=True)


def similarity(left: str | None, right: str | None) -> float:
    """0..1 similarity of two names after normalisation."""
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()
