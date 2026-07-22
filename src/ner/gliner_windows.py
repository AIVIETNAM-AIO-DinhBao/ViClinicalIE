from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from src.data_types import Chunk


_TOKEN_RE = re.compile(r"\S+", flags=re.UNICODE)


class TokenCounter(Protocol):
    def token_offsets(self, text: str) -> list[tuple[int, int]]: ...


class RegexTokenCounter:
    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in _TOKEN_RE.finditer(text)]


class TransformersTokenCounter:
    def __init__(self, tokenizer_name_or_path: str, *, revision: str | None = None, local_files_only: bool = False) -> None:
        self.tokenizer_name_or_path = tokenizer_name_or_path
        self.revision = revision
        self.local_files_only = local_files_only
        self.tokenizer = None

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        if self.tokenizer is None:
            from transformers import AutoTokenizer  # type: ignore

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_name_or_path,
                use_fast=True,
                revision=self.revision,
                local_files_only=self.local_files_only,
            )
        encoded = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        return [(int(start), int(end)) for start, end in encoded["offset_mapping"] if int(end) > int(start)]


@dataclass(frozen=True, slots=True)
class GLiNERWindow:
    window_id: str
    parent_chunk_id: int
    text: str
    start: int
    end: int
    token_count: int
    section: str | None = None
    subsection: str | None = None
    source_chunk_ids: tuple[int, ...] = ()
    token_start: int = 0
    token_end: int = 0


@dataclass(frozen=True, slots=True)
class _ParentUnit:
    text: str
    start: int
    end: int
    section: str | None
    subsection: str | None
    source_chunk_ids: tuple[int, ...]


def build_gliner_windows(
    raw_text: str,
    chunks: Sequence[Chunk],
    *,
    max_tokens: int = 320,
    overlap_tokens: int = 64,
    counter: TokenCounter | None = None,
    strategy: str = "legacy_chunk",
) -> list[GLiNERWindow]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must satisfy 0 <= overlap_tokens < max_tokens")
    tokenizer = counter or RegexTokenCounter()
    parents = _build_parent_units(raw_text, list(chunks), strategy=strategy)
    windows: list[GLiNERWindow] = []
    for parent_id, chunk in enumerate(parents):
        if raw_text[chunk.start:chunk.end] != chunk.text:
            raise ValueError(f"Chunk offset mismatch for parent {parent_id}")
        offsets = tokenizer.token_offsets(chunk.text)
        if not offsets:
            continue
        step = max_tokens - overlap_tokens
        token_start = 0
        window_index = 0
        while token_start < len(offsets):
            token_end = min(len(offsets), token_start + max_tokens)
            local_start = offsets[token_start][0]
            local_end = offsets[token_end - 1][1]
            start = chunk.start + local_start
            end = chunk.start + local_end
            window_text = raw_text[start:end]
            windows.append(
                GLiNERWindow(
                    window_id=f"c{parent_id}:w{window_index}",
                    parent_chunk_id=parent_id,
                    text=window_text,
                    start=start,
                    end=end,
                    token_count=token_end - token_start,
                    section=chunk.section,
                    subsection=chunk.subsection,
                    source_chunk_ids=chunk.source_chunk_ids,
                    token_start=token_start,
                    token_end=token_end,
                )
            )
            if token_end == len(offsets):
                break
            token_start += step
            window_index += 1
    return windows


def _build_parent_units(raw_text: str, chunks: list[Chunk], *, strategy: str) -> list[_ParentUnit]:
    if not chunks:
        return [_ParentUnit(raw_text, 0, len(raw_text), None, None, ())]
    indexed = list(enumerate(chunks))
    if strategy in {"legacy_chunk", "line"}:
        return [_parent(raw_text, [item]) for item in indexed]
    if strategy == "line_bullet":
        groups: list[list[tuple[int, Chunk]]] = []
        current: list[tuple[int, Chunk]] = []
        for item in indexed:
            _, chunk = item
            if current and (chunk.line_id is None or chunk.line_id != current[0][1].line_id):
                groups.append(current)
                current = []
            current.append(item)
        if current:
            groups.append(current)
        return [_parent(raw_text, group) for group in groups]
    if strategy not in {"section", "section_no_overlap", "section_overlap"}:
        raise ValueError(f"Unknown GLiNER window strategy: {strategy}")

    parents: list[_ParentUnit] = []
    current: list[tuple[int, Chunk]] = []
    for item in indexed:
        _, chunk = item
        if not current:
            current = [item]
            continue
        same_section = chunk.section == current[0][1].section
        if same_section:
            current.append(item)
            continue
        parents.append(_parent(raw_text, current))
        current = [item]
    if current:
        parents.append(_parent(raw_text, current))
    return parents


def _parent(raw_text: str, indexed_chunks: list[tuple[int, Chunk]]) -> _ParentUnit:
    first, last = indexed_chunks[0][1], indexed_chunks[-1][1]
    return _ParentUnit(
        text=raw_text[first.start:last.end],
        start=first.start,
        end=last.end,
        section=first.section,
        subsection=first.subsection,
        source_chunk_ids=tuple(index for index, _ in indexed_chunks),
    )