from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import AppConfig
from src.data_types import FinalEntity, MappingCandidate
from src.linking.candidate_selector import CandidateSelectionConfig, select_candidates
from src.linking.sparse_retriever import BM25AliasRetriever, SparseAliasRetriever
from src.linking.terminology_normalizer import normalize_for_lookup, normalize_no_diacritics_for_lookup


DEFAULT_ABBREVIATIONS: dict[str, str] = {
    "gerd": "trào ngược dạ dày thực quản",
    "copd": "bệnh phổi tắc nghẽn mạn tính",
    "uti": "nhiễm khuẩn đường tiết niệu",
    "mi": "nhồi máu cơ tim",
}


class ICD10Linker:
    """Attach ICD-10 candidate codes to diagnosis entities.

    The linker is intentionally downstream-only: it never creates, removes,
    moves, expands, trims, or retypes entities. It only updates the `candidates`
    field and `provenance["icd10_linking"]` for `CHẨN_ĐOÁN` entities.
    """

    def __init__(
        self,
        processed_dir: str | Path,
        config: dict[str, Any] | None = None,
        *,
        aliases: pd.DataFrame | None = None,
        index: pd.DataFrame | None = None,
        tfidf_retriever: SparseAliasRetriever | None = None,
        bm25_retriever: BM25AliasRetriever | None = None,
    ) -> None:
        self.processed_dir = Path(processed_dir)
        self.config = config or {}
        self.diagnosis_types = set(self.config.get("diagnosis_types", ["CHẨN_ĐOÁN"]))
        retrieval_cfg = self.config.get("retrieval", {}) if isinstance(self.config.get("retrieval", {}), dict) else {}
        self.top_k_exact = int(retrieval_cfg.get("top_k_exact", 20))
        self.top_k_tfidf = int(retrieval_cfg.get("top_k_tfidf", 20))
        self.top_k_bm25 = int(retrieval_cfg.get("top_k_bm25", 20))
        self.selection_config = CandidateSelectionConfig.from_dict(self.config.get("selection", {}))
        selection_cfg = self.config.get("selection", {}) if isinstance(self.config.get("selection", {}), dict) else {}
        self.min_retrieval_similarity = float(selection_cfg.get("min_retrieval_similarity", 0.55))
        self.min_sparse_query_tokens = int(selection_cfg.get("min_sparse_query_tokens", 2))
        self.min_sparse_query_chars = int(selection_cfg.get("min_sparse_query_chars", 6))

        self.aliases = aliases if aliases is not None else pd.read_parquet(self.processed_dir / "icd10_aliases.parquet")
        self.index = index if index is not None else pd.read_parquet(self.processed_dir / "icd10_index.parquet")
        self.valid_codes = {str(code) for code in self.index.get("code", []) if str(code)}
        self._canonical_by_code = self.index.set_index("code", drop=False).to_dict(orient="index") if not self.index.empty else {}
        self.tfidf_retriever = tfidf_retriever
        self.bm25_retriever = bm25_retriever
        self._candidate_cache: dict[str, list[MappingCandidate]] = {}

    @classmethod
    def from_config(cls, config: AppConfig) -> "ICD10Linker":
        return cls(config.path("processed_dir"), config.raw.get("icd10_linking", {}))

    def link_entities(self, entities: list[FinalEntity], raw_text: str | None = None) -> list[FinalEntity]:
        return [self.link_entity(entity, raw_text=raw_text) for entity in entities]

    def link_entity(self, entity: FinalEntity, raw_text: str | None = None) -> FinalEntity:
        if str(entity.type) not in self.diagnosis_types:
            return entity

        context = self._entity_context(entity, raw_text)
        candidates = self.generate_candidates(entity.text, context=context)
        selected = select_candidates(candidates, self.selection_config)
        provenance = dict(entity.provenance)
        provenance["icd10_linking"] = {
            "query": entity.text,
            "query_variants": normalize_diagnosis_queries(entity.text, self.config),
            "chosen": [_candidate_to_log(candidate) for candidate in selected.chosen_candidates],
            "top_candidates": [_candidate_to_log(candidate) for candidate in candidates[:10]],
            "selection_reason": selected.reason,
            "thresholds": {
                "max_candidates": self.selection_config.max_candidates,
                "min_score_top1": self.selection_config.min_score_top1,
                "include_second_if_within": self.selection_config.include_second_if_within,
                "min_score_additional": self.selection_config.min_score_additional,
            },
        }
        return replace(entity, candidates=selected.chosen_codes, provenance=provenance)

    def generate_candidates(self, mention: str, context: str = "") -> list[MappingCandidate]:
        del context  # reserved for later deterministic context boosts/reranking
        cache_key = normalize_for_lookup(mention)
        if cache_key in self._candidate_cache:
            return list(self._candidate_cache[cache_key])
        candidates: list[MappingCandidate] = []
        query_variants = normalize_diagnosis_queries(mention, self.config)
        for query in query_variants:
            candidates.extend(self._exact_alias_candidates(query))
            if self._allow_sparse_query(query):
                candidates.extend(self._tfidf_candidates(query))
                candidates.extend(self._bm25_candidates(query))
        merged = self._merge_candidates(candidates)
        self._candidate_cache[cache_key] = merged
        return list(merged)

    def _allow_sparse_query(self, query: str) -> bool:
        normalized = normalize_for_lookup(query)
        tokens = normalized.split()
        if normalized in DEFAULT_ABBREVIATIONS:
            return True
        return len(tokens) >= self.min_sparse_query_tokens and len(normalized) >= self.min_sparse_query_chars

    def _exact_alias_candidates(self, query: str) -> list[MappingCandidate]:
        if self.aliases.empty:
            return []
        norm = normalize_for_lookup(query)
        no_diac = normalize_no_diacritics_for_lookup(query)
        mask = (self.aliases.get("alias_norm", "") == norm) | (self.aliases.get("alias_no_diacritics", "") == no_diac)
        rows = self.aliases[mask].head(self.top_k_exact)
        output: list[MappingCandidate] = []
        for idx, row in rows.iterrows():
            code = str(row.get("code", ""))
            if not code or code not in self.valid_codes:
                continue
            output.append(
                MappingCandidate(
                    code=code,
                    name=_candidate_name(row, self._canonical_by_code.get(code, {})),
                    terminology="ICD10",
                    lexical_score=1.0,
                    final_score=1.0,
                    metadata={
                        "alias": str(row.get("alias", "")),
                        "alias_source": str(row.get("alias_source", "")),
                        "row_index": int(idx),
                        "retriever": "exact_alias",
                        "match_type": "exact_alias",
                    },
                )
            )
        return output

    def _tfidf_candidates(self, query: str) -> list[MappingCandidate]:
        if self.top_k_tfidf <= 0:
            return []
        retriever = self._get_tfidf_retriever()
        if retriever is None:
            return []
        return [
            candidate
            for candidate in (self._normalize_retrieved(candidate, query, "tfidf") for candidate in retriever.query(query, top_k=self.top_k_tfidf))
            if candidate.final_score >= self.min_retrieval_similarity
        ]

    def _bm25_candidates(self, query: str) -> list[MappingCandidate]:
        if self.top_k_bm25 <= 0:
            return []
        retriever = self._get_bm25_retriever()
        if retriever is None:
            return []
        return [
            candidate
            for candidate in (self._normalize_retrieved(candidate, query, "bm25") for candidate in retriever.query(query, top_k=self.top_k_bm25))
            if candidate.final_score >= self.min_retrieval_similarity
        ]

    def _get_tfidf_retriever(self) -> SparseAliasRetriever | None:
        if self.tfidf_retriever is not None:
            return self.tfidf_retriever
        try:
            self.tfidf_retriever = SparseAliasRetriever.from_processed(self.processed_dir, kind="icd")
        except FileNotFoundError:
            return None
        return self.tfidf_retriever

    def _get_bm25_retriever(self) -> BM25AliasRetriever | None:
        if self.bm25_retriever is not None:
            return self.bm25_retriever
        try:
            self.bm25_retriever = BM25AliasRetriever.from_processed(self.processed_dir, kind="icd")
        except FileNotFoundError:
            return None
        return self.bm25_retriever

    def _normalize_retrieved(self, candidate: MappingCandidate, query: str, retriever_name: str) -> MappingCandidate:
        metadata = dict(candidate.metadata)
        metadata["retriever"] = metadata.get("retriever", retriever_name)
        score = candidate.final_score
        if retriever_name == "bm25":
            score = score / (score + 1.0) if score > 0 else 0.0
        sparse_score = max(0.0, min(float(score), 1.0))
        similarity = _alias_query_similarity(query, str(metadata.get("alias", candidate.name)))
        score = min(sparse_score, similarity)
        metadata["sparse_score_normalized"] = round(sparse_score, 6)
        metadata["query_alias_similarity"] = round(similarity, 6)
        return replace(candidate, final_score=score, metadata=metadata)

    def _merge_candidates(self, candidates: list[MappingCandidate]) -> list[MappingCandidate]:
        best_by_code: dict[str, MappingCandidate] = {}
        for candidate in candidates:
            code = str(candidate.code)
            if not code or code not in self.valid_codes:
                continue
            current = best_by_code.get(code)
            if current is None or candidate.final_score > current.final_score:
                best_by_code[code] = candidate
        return sorted(best_by_code.values(), key=lambda item: item.final_score, reverse=True)

    @staticmethod
    def _entity_context(entity: FinalEntity, raw_text: str | None) -> str:
        if not raw_text:
            return ""
        start = max(0, entity.start - 160)
        end = min(len(raw_text), entity.end + 160)
        return raw_text[start:end]


def normalize_diagnosis_queries(text: str, config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or {}
    normalization_cfg = cfg.get("normalization", {}) if isinstance(cfg.get("normalization", {}), dict) else {}
    expand_abbreviations = bool(normalization_cfg.get("expand_common_abbreviations", True))
    use_no_diacritics = bool(normalization_cfg.get("use_no_diacritics", True))

    variants = [text, normalize_for_lookup(text)]
    if use_no_diacritics:
        variants.append(normalize_no_diacritics_for_lookup(text))
    if expand_abbreviations:
        key = normalize_for_lookup(text)
        expanded = DEFAULT_ABBREVIATIONS.get(key)
        if expanded:
            variants.extend([expanded, normalize_no_diacritics_for_lookup(expanded)])

    seen: set[str] = set()
    output: list[str] = []
    for variant in variants:
        value = str(variant).strip()
        if not value:
            continue
        dedupe_key = normalize_for_lookup(value)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        output.append(value)
    return output


def _candidate_name(row: pd.Series, canonical: dict[str, Any]) -> str:
    return str(
        row.get("canonical_name_vi")
        or canonical.get("canonical_name_vi")
        or row.get("canonical_name_en")
        or canonical.get("canonical_name_en")
        or row.get("alias", "")
    )


def _candidate_to_log(candidate: MappingCandidate) -> dict[str, Any]:
    return {
        "code": candidate.code,
        "name": candidate.name,
        "score": round(float(candidate.final_score), 6),
        "lexical_score": round(float(candidate.lexical_score), 6),
        "alias": candidate.metadata.get("alias", ""),
        "alias_source": candidate.metadata.get("alias_source", ""),
        "source": candidate.metadata.get("retriever", candidate.metadata.get("match_type", "")),
        "query_alias_similarity": candidate.metadata.get("query_alias_similarity"),
    }


def _alias_query_similarity(query: str, alias: str) -> float:
    query_tokens = set(normalize_for_lookup(query).split())
    alias_tokens = set(normalize_for_lookup(alias).split())
    if not query_tokens or not alias_tokens:
        return 0.0
    overlap = query_tokens & alias_tokens
    if not overlap:
        return 0.0
    precision = len(overlap) / len(alias_tokens)
    recall = len(overlap) / len(query_tokens)
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    length_ratio = min(len(query_tokens), len(alias_tokens)) / max(len(query_tokens), len(alias_tokens))
    # Favor candidates whose alias is lexically close to the mention. This
    # prevents high BM25 scores from short/generic overlaps such as "bệnh nhân".
    return max(0.0, min(1.0, 0.80 * f1 + 0.20 * length_ratio))