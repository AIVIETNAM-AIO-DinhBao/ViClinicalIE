from __future__ import annotations

from src.data_types import Chunk
from src.ner.gliner_windows import build_gliner_windows


def test_long_chunk_creates_raw_aligned_overlap_windows() -> None:
    raw = "một hai ba bốn năm sáu"
    chunk = Chunk(raw, 0, len(raw), section="CURRENT")
    windows = build_gliner_windows(raw, [chunk], max_tokens=4, overlap_tokens=2)
    assert len(windows) == 2
    assert windows[0].text == "một hai ba bốn"
    assert windows[1].text == "ba bốn năm sáu"
    assert all(raw[window.start:window.end] == window.text for window in windows)
    assert all(window.section == "CURRENT" for window in windows)


def test_section_windows_pack_lines_without_crossing_section_boundary() -> None:
    raw = "một hai\nba bốn\nnăm sáu"
    chunks = [
        Chunk("một hai", 0, 7, section="A"),
        Chunk("ba bốn", 8, 14, section="A"),
        Chunk("năm sáu", 15, len(raw), section="B"),
    ]
    windows = build_gliner_windows(raw, chunks, max_tokens=20, overlap_tokens=0, strategy="section")
    assert [window.text for window in windows] == ["một hai\nba bốn", "năm sáu"]
    assert [window.section for window in windows] == ["A", "B"]
    assert all(raw[window.start:window.end] == window.text for window in windows)


def test_line_bullet_reassembles_structural_chunks_from_same_line() -> None:
    raw = "- đau ngực dữ dội\r\nDòng khác"
    chunks = [
        Chunk("- đau ngực", 0, 10, section="A", line_id=0, bullet_level=1),
        Chunk("dữ dội", 11, 17, section="A", line_id=0, bullet_level=1),
        Chunk("Dòng khác", 19, len(raw), section="A", line_id=1),
    ]
    windows = build_gliner_windows(raw, chunks, max_tokens=20, overlap_tokens=0, strategy="line_bullet")
    assert [window.text for window in windows] == ["- đau ngực dữ dội", "Dòng khác"]
    assert windows[0].source_chunk_ids == (0, 1)
    assert windows[1].source_chunk_ids == (2,)