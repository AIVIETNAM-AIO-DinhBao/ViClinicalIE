from __future__ import annotations

from src.data_types import Chunk
from src.ner.gliner_windows import build_gliner_windows


def _chunks(raw: str) -> list[Chunk]:
    first = "Mục Á\r\nmột hai ba bốn năm"
    second_start = len(first) + 2
    return [
        Chunk(first, 0, len(first), section="A", subsection="first", line_id=0),
        Chunk("sáu bảy tám chín mười", second_start, len(raw), section="A", subsection="second", line_id=1),
    ]


def test_section_windows_preserve_crlf_unicode_substrings_and_subsection_does_not_split() -> None:
    raw = "Mục Á\r\nmột hai ba bốn năm\r\nsáu bảy tám chín mười"
    windows = build_gliner_windows(raw, _chunks(raw), max_tokens=30, overlap_tokens=0, strategy="section_no_overlap")
    assert len(windows) == 1
    assert windows[0].text == raw
    assert windows[0].source_chunk_ids == (0, 1)
    assert raw[windows[0].start:windows[0].end] == windows[0].text


def test_section_overlap_covers_boundary_entity_and_exact_windows_are_deterministic() -> None:
    raw = "một hai ba bốn năm sáu bảy tám chín mười"
    chunks = [Chunk(raw, 0, len(raw), section="A", line_id=0)]
    first = build_gliner_windows(raw, chunks, max_tokens=6, overlap_tokens=2, strategy="section_overlap")
    second = build_gliner_windows(raw, chunks, max_tokens=6, overlap_tokens=2, strategy="section_overlap")
    assert first == second
    assert [window.text for window in first] == ["một hai ba bốn năm sáu", "năm sáu bảy tám chín mười"]
    entity = "năm sáu"
    start = raw.index(entity)
    end = start + len(entity)
    assert sum(window.start <= start and end <= window.end for window in first) == 2


def test_long_section_does_not_lose_tail() -> None:
    raw = " ".join(f"t{i}" for i in range(13))
    windows = build_gliner_windows(raw, [Chunk(raw, 0, len(raw), section="A")], max_tokens=5, overlap_tokens=0, strategy="section_no_overlap")
    assert windows[-1].text.endswith("t12")
    assert windows[-1].end == len(raw)
    assert [window.token_count for window in windows] == [5, 5, 3]


def test_section_boundary_is_never_crossed() -> None:
    raw = "a b c\r\nd e f"
    chunks = [
        Chunk("a b c", 0, 5, section="A"),
        Chunk("d e f", 7, len(raw), section="B"),
    ]
    windows = build_gliner_windows(raw, chunks, max_tokens=20, overlap_tokens=0, strategy="section_no_overlap")
    assert [window.section for window in windows] == ["A", "B"]
    assert all(not (window.start < 7 and window.end > 7) for window in windows)