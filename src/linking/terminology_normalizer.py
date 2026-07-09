from __future__ import annotations

import re
import unicodedata
from typing import Iterable


_WHITESPACE_RE = re.compile(r"\s+")
_LIGHT_PUNCT_RE = re.compile(r"[\[\]{}()\"“”‘’`]+")
_SEPARATOR_RE = re.compile(r"[,;:]+")
_TOKEN_SPLIT_RE = re.compile(r"[^\w%+./-]+", flags=re.UNICODE)


def ensure_text(value: object) -> str:
    """Return a safe string for terminology values loaded from CSV/RRF files."""

    if value is None:
        return ""
    return str(value)


def normalize_unicode(text: object) -> str:
    return unicodedata.normalize("NFC", ensure_text(text))


def normalize_whitespace(text: object) -> str:
    return _WHITESPACE_RE.sub(" ", ensure_text(text)).strip()


def remove_vietnamese_diacritics(text: object) -> str:
    """Remove Vietnamese tone marks and normalize đ/Đ for accent-insensitive lookup."""

    normalized = unicodedata.normalize("NFD", ensure_text(text))
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", without_marks).replace("đ", "d").replace("Đ", "D")


def strip_punctuation_light(text: object) -> str:
    """Remove punctuation that usually hurts lookup while preserving medical separators."""

    value = ensure_text(text)
    value = _LIGHT_PUNCT_RE.sub(" ", value)
    value = _SEPARATOR_RE.sub(" ", value)
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    return normalize_whitespace(value)


def normalize_for_lookup(text: object) -> str:
    value = normalize_unicode(text).lower()
    value = strip_punctuation_light(value)
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\s*-\s*", "-", value)
    return normalize_whitespace(value)


def normalize_no_diacritics_for_lookup(text: object) -> str:
    return normalize_for_lookup(remove_vietnamese_diacritics(text))


def normalize_code(code: object) -> str:
    return ensure_text(code).strip().upper()


def dotless_code(code: object) -> str:
    return normalize_code(code).replace(".", "")


def tokenize_for_lookup(text: object) -> list[str]:
    value = normalize_for_lookup(text)
    return [token for token in _TOKEN_SPLIT_RE.split(value) if token]


def unique_non_empty(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = normalize_whitespace(normalize_unicode(value))
        if not text:
            continue
        key = normalize_for_lookup(text)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def guess_alias_lang(alias: object) -> str:
    text = ensure_text(alias)
    lowered = text.lower()
    if any(ch in lowered for ch in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"):
        return "vi"
    vietnamese_markers = ("benh", "bệnh", "viem", "viêm", "suy", "ung thư", "nhiễm", "tăng", "giảm")
    if any(marker in lowered for marker in vietnamese_markers):
        return "vi"
    return "en"
