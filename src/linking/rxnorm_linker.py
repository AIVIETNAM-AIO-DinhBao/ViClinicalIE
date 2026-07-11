from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import AppConfig
from src.data_types import FinalEntity, MappingCandidate
from src.linking.candidate_selector import CandidateSelectionConfig, select_candidates
from src.linking.drug_parser import ParsedDrug, parse_drug_mention
from src.linking.sparse_retriever import BM25AliasRetriever, SparseAliasRetriever
from src.linking.terminology_normalizer import normalize_for_lookup, normalize_no_diacritics_for_lookup


CLINICAL_TTYS = {"SCD", "SBD", "GPCK", "BPCK", "SCDC", "SBDC", "SCDF", "SBDF"}
NAME_ONLY_TTYS = {"IN", "PIN", "MIN", "BN"}


class RxNormLinker:
    """Attach RxNorm RxCUI candidates to drug entities only."""

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
        self.drug_types = set(self.config.get("drug_types", ["THUỐC"]))
        retrieval_cfg = self.config.get("retrieval", {}) if isinstance(self.config.get("retrieval", {}), dict) else {}
        selection_cfg = self.config.get("selection", {}) if isinstance(self.config.get("selection", {}), dict) else {}
        scoring_cfg = self.config.get("scoring", {}) if isinstance(self.config.get("scoring", {}), dict) else {}
        self.parser_config = self.config.get("parser", {}) if isinstance(self.config.get("parser", {}), dict) else {}
        self.top_k_exact = int(retrieval_cfg.get("top_k_exact", 20))
        self.top_k_tfidf = int(retrieval_cfg.get("top_k_tfidf", 20))
        self.top_k_bm25 = int(retrieval_cfg.get("top_k_bm25", 20))
        self.selection_config = CandidateSelectionConfig.from_dict(selection_cfg)
        self.min_retrieval_similarity = float(selection_cfg.get("min_retrieval_similarity", 0.55))
        self.min_sparse_query_chars = int(selection_cfg.get("min_sparse_query_chars", 3))
        self.exact_full_mention_score = float(scoring_cfg.get("exact_full_mention_score", 1.0))
        self.exact_name_score = float(scoring_cfg.get("exact_name_score", 0.90))
        self.manual_alias_boost = float(scoring_cfg.get("manual_alias_boost", 0.05))
        self.strength_match_boost = float(scoring_cfg.get("strength_match_boost", 0.10))
        self.unit_match_boost = float(scoring_cfg.get("unit_match_boost", 0.05))
        self.strength_mismatch_penalty = float(scoring_cfg.get("strength_mismatch_penalty", 0.20))
        self.no_strength_clinical_drug_penalty = float(scoring_cfg.get("no_strength_clinical_drug_penalty", 0.10))

        self.aliases = aliases if aliases is not None else pd.read_parquet(self.processed_dir / "rxnorm_aliases.parquet")
        self.index = index if index is not None else pd.read_parquet(self.processed_dir / "rxnorm_index.parquet")
        self.valid_codes = {str(code) for code in self.index.get("rxcui", []) if str(code)}
        self._canonical_by_code = self.index.set_index("rxcui", drop=False).to_dict(orient="index") if not self.index.empty else {}
        self.tfidf_retriever = tfidf_retriever
        self.bm25_retriever = bm25_retriever
        self._candidate_cache: dict[str, list[MappingCandidate]] = {}

    @classmethod
    def from_config(cls, config: AppConfig) -> "RxNormLinker":
        return cls(config.path("processed_dir"), config.raw.get("rxnorm_linking", {}))

    def link_entities(self, entities: list[FinalEntity], raw_text: str | None = None) -> list[FinalEntity]:
        return [self.link_entity(entity, raw_text=raw_text) for entity in entities]

    def link_entity(self, entity: FinalEntity, raw_text: str | None = None) -> FinalEntity:
        if str(entity.type) not in self.drug_types:
            return entity
        context = self._entity_context(entity, raw_text)
        parsed = parse_drug_mention(entity.text, self.parser_config)
        candidates = self.generate_candidates(entity.text, context=context)
        selected = select_candidates(candidates, self.selection_config)
        provenance = dict(entity.provenance)
        provenance["rxnorm_linking"] = {
            "query": entity.text,
            "parsed": parsed.to_dict(),
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
        del context
        cache_key = normalize_for_lookup(mention)
        if cache_key in self._candidate_cache:
            return list(self._candidate_cache[cache_key])
        parsed = parse_drug_mention(mention, self.parser_config)
        candidates: list[MappingCandidate] = []
        queries = _query_variants(mention, parsed)
        for query, query_kind in queries:
            candidates.extend(self._exact_alias_candidates(query, query_kind, parsed))
        candidates.extend(self._ingredient_strength_candidates(parsed))
        for query, _query_kind in queries:
            if self._allow_sparse_query(query):
                candidates.extend(self._tfidf_candidates(query, parsed))
                candidates.extend(self._bm25_candidates(query, parsed))
        merged = self._merge_candidates(candidates, parsed)
        self._candidate_cache[cache_key] = merged
        return list(merged)

    def _exact_alias_candidates(self, query: str, query_kind: str, parsed: ParsedDrug) -> list[MappingCandidate]:
        if self.aliases.empty:
            return []
        norm = normalize_for_lookup(query)
        no_diac = normalize_no_diacritics_for_lookup(query)
        mask = (self.aliases.get("alias_norm", "") == norm) | (self.aliases.get("alias_no_diacritics", "") == no_diac)
        rows = self.aliases[mask].head(self.top_k_exact)
        output: list[MappingCandidate] = []
        base_score = self.exact_full_mention_score if query_kind == "full" else self.exact_name_score
        for idx, row in rows.iterrows():
            code = str(row.get("rxcui", ""))
            if not code or code not in self.valid_codes:
                continue
            candidate = MappingCandidate(
                code=code,
                name=_candidate_name(row, self._canonical_by_code.get(code, {})),
                terminology="RXNORM",
                lexical_score=base_score,
                final_score=base_score,
                metadata=_row_metadata(row, idx, "exact_alias", "exact_alias"),
            )
            output.append(self._apply_constraints(candidate, parsed, exact=True))
        return output

    def _ingredient_strength_candidates(self, parsed: ParsedDrug) -> list[MappingCandidate]:
        if not parsed.normalized_name or parsed.strength_value is None or self.aliases.empty:
            return []
        name = parsed.normalized_name
        rows = self.aliases[
            (self.aliases.get("ingredient_guess", "").map(normalize_for_lookup) == name)
            & (self.aliases.get("strength_value").map(_float_or_none) == parsed.strength_value)
        ].head(self.top_k_exact)
        output: list[MappingCandidate] = []
        for idx, row in rows.iterrows():
            code = str(row.get("rxcui", ""))
            if not code or code not in self.valid_codes:
                continue
            candidate = MappingCandidate(
                code=code,
                name=_candidate_name(row, self._canonical_by_code.get(code, {})),
                terminology="RXNORM",
                lexical_score=0.88,
                final_score=0.88,
                metadata=_row_metadata(row, idx, "ingredient_strength", "ingredient_strength"),
            )
            output.append(self._apply_constraints(candidate, parsed, exact=True))
        return output

    def _tfidf_candidates(self, query: str, parsed: ParsedDrug) -> list[MappingCandidate]:
        if self.top_k_tfidf <= 0:
            return []
        retriever = self._get_tfidf_retriever()
        if retriever is None:
            return []
        return [
            candidate
            for candidate in (self._normalize_retrieved(candidate, query, parsed, "tfidf") for candidate in retriever.query(query, top_k=self.top_k_tfidf))
            if candidate.final_score >= self.min_retrieval_similarity
        ]

    def _bm25_candidates(self, query: str, parsed: ParsedDrug) -> list[MappingCandidate]:
        if self.top_k_bm25 <= 0:
            return []
        retriever = self._get_bm25_retriever()
        if retriever is None:
            return []
        return [
            candidate
            for candidate in (self._normalize_retrieved(candidate, query, parsed, "bm25") for candidate in retriever.query(query, top_k=self.top_k_bm25))
            if candidate.final_score >= self.min_retrieval_similarity
        ]

    def _normalize_retrieved(self, candidate: MappingCandidate, query: str, parsed: ParsedDrug, retriever_name: str) -> MappingCandidate:
        metadata = dict(candidate.metadata)
        metadata["retriever"] = metadata.get("retriever", retriever_name)
        score = candidate.final_score
        if retriever_name == "bm25":
            score = score / (score + 1.0) if score > 0 else 0.0
        sparse_score = max(0.0, min(float(score), 1.0))
        similarity = _alias_query_similarity(query, str(metadata.get("alias", candidate.name)), parsed)
        constrained = replace(candidate, final_score=min(sparse_score, similarity), metadata=metadata)
        constrained.metadata["sparse_score_normalized"] = round(sparse_score, 6)
        constrained.metadata["query_alias_similarity"] = round(similarity, 6)
        return self._apply_constraints(constrained, parsed, exact=False)

    def _apply_constraints(self, candidate: MappingCandidate, parsed: ParsedDrug, *, exact: bool) -> MappingCandidate:
        metadata = dict(candidate.metadata)
        score = float(candidate.final_score)
        tty = str(metadata.get("tty", ""))
        alias_source = str(metadata.get("alias_source", ""))
        row_strength_value = _float_or_none(metadata.get("strength_value"))
        row_strength_unit = str(metadata.get("strength_unit", "") or "")
        if alias_source == "manual_brand":
            score += self.manual_alias_boost
        if parsed.strength_value is not None:
            if row_strength_value is not None and abs(row_strength_value - parsed.strength_value) < 1e-6:
                score += self.strength_match_boost
                if parsed.strength_unit and row_strength_unit == parsed.strength_unit:
                    score += self.unit_match_boost
                if tty in CLINICAL_TTYS:
                    score += 0.03
            elif row_strength_value is not None and not exact:
                score -= self.strength_mismatch_penalty
            elif row_strength_value is None:
                score -= self.strength_mismatch_penalty
        elif tty in CLINICAL_TTYS and not exact:
            score -= self.no_strength_clinical_drug_penalty
        if parsed.strength_value is None and tty in NAME_ONLY_TTYS:
            score += 0.04
        metadata["constraint_adjusted"] = True
        return replace(candidate, final_score=max(0.0, min(score, 1.0)), metadata=metadata)

    def _allow_sparse_query(self, query: str) -> bool:
        return len(normalize_for_lookup(query)) >= self.min_sparse_query_chars

    def _get_tfidf_retriever(self) -> SparseAliasRetriever | None:
        if self.tfidf_retriever is not None:
            return self.tfidf_retriever
        try:
            self.tfidf_retriever = SparseAliasRetriever.from_processed(self.processed_dir, kind="rx")
        except FileNotFoundError:
            return None
        return self.tfidf_retriever

    def _get_bm25_retriever(self) -> BM25AliasRetriever | None:
        if self.bm25_retriever is not None:
            return self.bm25_retriever
        try:
            self.bm25_retriever = BM25AliasRetriever.from_processed(self.processed_dir, kind="rx")
        except FileNotFoundError:
            return None
        return self.bm25_retriever

    def _merge_candidates(self, candidates: list[MappingCandidate], parsed: ParsedDrug) -> list[MappingCandidate]:
        if parsed.strength_value is not None and any(
            (value := _float_or_none(candidate.metadata.get("strength_value"))) is not None
            and abs(value - parsed.strength_value) < 1e-6
            for candidate in candidates
        ):
            candidates = [candidate for candidate in candidates if _float_or_none(candidate.metadata.get("strength_value")) is not None]
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


def _query_variants(mention: str, parsed: ParsedDrug) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = [(mention, "full")]
    if parsed.normalized_name:
        variants.append((parsed.normalized_name, "name"))
    seen: set[str] = set()
    output: list[tuple[str, str]] = []
    for query, kind in variants:
        key = normalize_for_lookup(query)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append((query, kind))
    return output


def _row_metadata(row: pd.Series, idx: Any, retriever: str, match_type: str | None = None) -> dict[str, Any]:
    metadata = {
        "alias": str(row.get("alias", "")),
        "alias_source": str(row.get("alias_source", "")),
        "row_index": int(idx),
        "retriever": retriever,
        "tty": str(row.get("tty", "")),
        "ingredient_guess": str(row.get("ingredient_guess", "")),
        "strength_value": row.get("strength_value"),
        "strength_unit": str(row.get("strength_unit", "") or ""),
        "dose_form_guess": str(row.get("dose_form_guess", "")),
        "is_clinical_drug": bool(row.get("is_clinical_drug", False)),
    }
    if match_type:
        metadata["match_type"] = match_type
    return metadata


def _candidate_name(row: pd.Series, canonical: dict[str, Any]) -> str:
    return str(row.get("alias") or canonical.get("str") or row.get("ingredient_guess") or "")


def _candidate_to_log(candidate: MappingCandidate) -> dict[str, Any]:
    return {
        "code": candidate.code,
        "name": candidate.name,
        "score": round(float(candidate.final_score), 6),
        "lexical_score": round(float(candidate.lexical_score), 6),
        "tty": candidate.metadata.get("tty", ""),
        "alias": candidate.metadata.get("alias", ""),
        "alias_source": candidate.metadata.get("alias_source", ""),
        "strength_value": candidate.metadata.get("strength_value"),
        "strength_unit": candidate.metadata.get("strength_unit", ""),
        "source": candidate.metadata.get("retriever", candidate.metadata.get("match_type", "")),
        "query_alias_similarity": candidate.metadata.get("query_alias_similarity"),
    }


def _alias_query_similarity(query: str, alias: str, parsed: ParsedDrug) -> float:
    query_tokens = set(normalize_for_lookup(query).split())
    alias_tokens = set(normalize_for_lookup(alias).split())
    if parsed.normalized_name:
        query_tokens |= set(parsed.normalized_name.split())
    if not query_tokens or not alias_tokens:
        return 0.0
    overlap = query_tokens & alias_tokens
    if not overlap:
        return 0.0
    precision = len(overlap) / len(alias_tokens)
    recall = len(overlap) / len(query_tokens)
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    length_ratio = min(len(query_tokens), len(alias_tokens)) / max(len(query_tokens), len(alias_tokens))
    return max(0.0, min(1.0, 0.80 * f1 + 0.20 * length_ratio))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None