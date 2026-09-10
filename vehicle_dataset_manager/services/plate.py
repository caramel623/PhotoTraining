"""Taiwan license-plate normalization and fuzzy matching.

The plate is a *label/grouping* signal only; it is never a Re-ID feature.

Two levels are provided:
* :func:`normalize_plate`  -> canonical string (uppercase, separators removed).
  Used for the stored ``plate_text_normalized`` and for *exact* grouping.
* :func:`fuzzy_key` / :func:`plates_fuzzy_match` -> collapse common OCR
  confusables (1/I, 0/O, 5/S, 8/B) so that near-matches can be grouped as
  *candidates* and reviewed, rather than being treated as totally different.

Examples (per project README):
    BFY-1765 / BFY1765 / BFY-I765 / BFY176S / 8FY1765
all normalize to close forms; ``BFY1765`` and ``8FY1765`` match fuzzy because
B and 8 are confusable, while the normalized exact keys differ.
"""
from __future__ import annotations

import re
from typing import Dict, List

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

#: OCR confusable equivalence classes. Each group maps to its canonical
#: representative (digits preferred) for grouping purposes.
_CONFUSABLES: List[str] = ["1Il|", "0OQo", "5Ss", "8Bb", "2Zz"]

_CHAR_CLASS: Dict[str, str] = {}
for _group in _CONFUSABLES:
    # canonical = prefer a digit, else the first char
    canonical = _group[0]
    for ch in _group:
        if ch.isdigit():
            canonical = ch
            break
    for ch in _group:
        _CHAR_CLASS[ch] = canonical


def normalize_plate(raw: str | None) -> str:
    """Return the canonical plate string (uppercase, alnum only) or ""."""
    if not raw:
        return ""
    s = raw.upper()
    # Replace common separator chars, then drop everything not A-Z0-9.
    s = _NON_ALNUM.sub("", s)
    return s


def fuzzy_key(plate: str) -> str:
    """Collapse OCR confusables of a (raw or normalized) plate to a key.

    Two plates that differ only by confusable characters produce the same key.
    """
    if not plate:
        return ""
    norm = normalize_plate(plate)
    out = []
    for ch in norm:
        out.append(_CHAR_CLASS.get(ch, ch))
    return "".join(out)


def plates_fuzzy_match(a: str, b: str) -> bool:
    """True if two plates match after confusable collapsing (and are non-empty)."""
    ka, kb = fuzzy_key(a), fuzzy_key(b)
    return bool(ka) and ka == kb


def is_plausible_plate(plate: str) -> bool:
    """Cheap sanity check used by OCR quality grading.

    Accepts the common Taiwan patterns without being overly strict, so it can
    be relaxed/extended later without changing callers.
    """
    norm = normalize_plate(plate)
    fk = fuzzy_key(plate)
    if not norm:
        return False
    # Plates always contain letters (car: 3 letters + digits; motor: digits +
    # 3 letters). All-digit strings are usually header times like "11:56:54".
    if not any(ch.isalpha() for ch in norm):
        return False
    # Exact Taiwan passenger-car plate: 3 letters + 4 digits.
    if re.fullmatch(r"[A-Z]{3}[0-9]{4}", norm):
        return True
    # Lenient: 6-7 alnum whose fuzzy key ends in four digits. This accepts
    # confusable readings such as "8FY1765" (a misread of "BFY1765").
    if 6 <= len(fk) <= 7 and fk[-4:].isdigit() and fk[:-4].isalnum():
        return True
    return False