"""Text normalization shared by every scanner.

Attackers hide instructions with zero-width characters, look-alike letters
from other alphabets, odd spacing and base64. Everything is normalized here
first so the scanners only have to match plain lowercase text.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(
    map(ord, "​‌‍‎‏⁠⁡⁢⁣﻿­"), None
)

# Common Cyrillic/Greek letters that look identical to Latin ones.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
        "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "һ": "h", "ԛ": "q",
        "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c", "Х": "x", "І": "i",
        "Т": "t", "М": "m", "Н": "h", "В": "b", "К": "k",
        "ο": "o", "α": "a", "ε": "e", "ι": "i", "ν": "v", "Ο": "o", "Α": "a",
        "Ι": "i", "Β": "b", "Ε": "e", "Κ": "k", "Μ": "m", "Ν": "n", "Τ": "t",
    }
)

_WS = re.compile(r"\s+")
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def normalize(text: str) -> str:
    """NFKC, strip invisible characters, fold look-alikes, collapse spaces, lowercase."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_ZERO_WIDTH).translate(_HOMOGLYPHS)
    return _WS.sub(" ", text).casefold().strip()


def decoded_base64_segments(text: str) -> list[str]:
    """Return readable text hidden inside base64 blobs."""
    found = []
    for match in _B64.finditer(text or ""):
        blob = match.group(0)
        try:
            raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        printable = sum(ch.isprintable() or ch.isspace() for ch in decoded)
        if decoded and printable / len(decoded) > 0.9:
            found.append(decoded)
    return found


def scan_view(text: str) -> str:
    """Normalized text plus any decoded base64, for scanners to search."""
    parts = [normalize(text)] + [normalize(seg) for seg in decoded_base64_segments(text)]
    return " \n ".join(parts)
