from __future__ import annotations

from src.linking.drug_parser import parse_drug_mention


def test_parse_metoprolol_with_strength_route_frequency() -> None:
    parsed = parse_drug_mention("metoprolol 25mg po bid")
    assert parsed.normalized_name == "metoprolol"
    assert parsed.strength_value == 25.0
    assert parsed.strength_unit == "MG"
    assert parsed.route == "po"
    assert parsed.frequency == "bid"
    assert not parsed.is_combination


def test_parse_aspirin_x1_frequency() -> None:
    parsed = parse_drug_mention("aspirin 325mg x 1")
    assert parsed.normalized_name == "aspirin"
    assert parsed.strength_value == 325.0
    assert parsed.strength_unit == "MG"
    assert parsed.frequency == "x 1"


def test_parse_name_only_drugs() -> None:
    assert parse_drug_mention("atenolol").normalized_name == "atenolol"
    assert parse_drug_mention("omeprazole").normalized_name == "omeprazole"


def test_parse_gram_strength_and_iv_route() -> None:
    vancomycin = parse_drug_mention("vancomycin 1 gram")
    assert vancomycin.normalized_name == "vancomycin"
    assert vancomycin.strength_value == 1.0
    assert vancomycin.strength_unit == "G"

    levofloxacin = parse_drug_mention("levofloxacin 750mg iv")
    assert levofloxacin.normalized_name == "levofloxacin"
    assert levofloxacin.strength_value == 750.0
    assert levofloxacin.strength_unit == "MG"
    assert levofloxacin.route == "iv"


def test_parse_combination_marker_and_nebs() -> None:
    parsed = parse_drug_mention("albuterol / ipratropium nebs x2")
    assert parsed.normalized_name == "albuterol ipratropium"
    assert parsed.dose_form == "nebs"
    assert parsed.frequency == "x2"
    assert parsed.is_combination
