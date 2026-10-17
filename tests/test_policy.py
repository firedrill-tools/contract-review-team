"""Literal document completeness and honest model-review packet policy."""

from copy import deepcopy
from pathlib import Path

import pytest

from contract_review.document import entire, notice_candidates, parse_markdown, span
from contract_review.policy import extraction, packet, reconcile, review, summary
from contract_review.strict import sha256

FIXTURE = Path(__file__).parents[1] / "fixtures/meridian-services.md"


def document():
    return parse_markdown(FIXTURE.read_bytes())


def extracted(doc):
    return {
        "units": [
            {
                "unit_id": item["id"],
                "evidence": entire(item),
                "party": None,
                "action": entire(item),
                "deadline": None,
                "uncertainty": "Semantic interpretation remains a model proposal for human review.",
            }
            for item in doc["units"]
        ]
    }


def assessed(doc):
    return {
        "overall": "medium",
        "assessments": [
            {
                "unit_id": item["id"],
                "evidence": entire(item),
                "severity": "medium",
                "rationale": "Explicit synthetic human review example.",
                "uncertainty": "",
            }
            for item in doc["units"]
        ],
        "cross_references": [],
    }


def summarized(doc, compared):
    return {
        "narrative": "The model proposes this source-based review summary. Human legal review remains necessary.",
        "unit_ids": [item["id"] for item in doc["units"]],
        "notice_candidate_ids": [item["id"] for item in compared["notice_candidates"]],
        "rating_difference_ids": [
            item["id"] for item in compared["rating_differences"]
        ],
    }


def test_original_markdown_bytes_all_clauses_units_and_global_source_spans_are_literal():
    original = FIXTURE.read_bytes()
    doc = document()
    assert doc["sha256"] == sha256(original) and doc["bytes"] == len(original)
    assert [item["id"] for item in doc["clauses"]] == [f"C{i}" for i in range(1, 7)]
    assert [item["id"] for item in doc["units"]] == [f"C{i}-P1" for i in range(1, 7)]
    for item in doc["units"]:
        assert doc["text"][item["start"] : item["end"]] == item["quote"]
        span(doc, entire(item), item)
    assert FIXTURE.read_bytes() == original


def test_multiline_unicode_paragraphs_keep_codepoint_spans_and_every_nonwhitespace_source_character():
    raw = "# Synthetic Zoë Agreement\n\n## C1 Terms\n  Café and café are literal distinct strings.\nThis is the same paragraph.\n\n  The customer has a second obligation.  \n".encode()
    doc = parse_markdown(raw)
    assert len(doc["units"]) == 2
    assert "\n" in doc["units"][0]["quote"] and "café" in doc["units"][0]["quote"]
    clause = doc["clauses"][0]
    positions = {i for item in doc["units"] for i in range(item["start"], item["end"])}
    assert all(
        i in positions or doc["text"][i].isspace()
        for i in range(clause["start"], clause["end"])
    )
    assert doc["text"].encode() == raw


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "title",
        "utf8",
        "nul",
        "crlf",
        "gap",
        "duplicate",
        "unknown_heading",
        "preamble",
        "empty_clause",
        "second_title",
    ],
)
def test_unsupported_or_omitted_original_markdown_refuses(fault):
    raw = FIXTURE.read_bytes()
    if fault == "empty":
        raw = b""
    elif fault == "title":
        raw = raw.replace(b"# Meridian", b"Meridian", 1)
    elif fault == "utf8":
        raw = b"\xff" + raw
    elif fault == "nul":
        raw += b"\x00"
    elif fault == "crlf":
        raw = raw.replace(b"\n", b"\r\n")
    elif fault == "gap":
        raw = raw.replace(b"## C2 ", b"## C8 ")
    elif fault == "duplicate":
        raw = raw.replace(b"## C2 ", b"## C1 ")
    elif fault == "unknown_heading":
        raw += b"\n## Appendix\nA substantive unnumbered condition.\n"
    elif fault == "preamble":
        raw = raw.replace(b"\n\n## C1", b"\nPayment is immediately due.\n\n## C1", 1)
    elif fault == "empty_clause":
        raw = raw[: raw.index(b"## C6")] + b"## C6 Empty\n\n"
    else:
        raw += b"\n# Another document\n"
    with pytest.raises((ValueError, UnicodeError)):
        parse_markdown(raw)


@pytest.mark.parametrize("fault", ["bool", "float", "outside", "different_quote"])
def test_original_span_offsets_and_quote_types_cannot_be_coerced(fault):
    doc = document()
    item = doc["units"][0]
    value = entire(item)
    if fault == "bool":
        value["start"] = True
    elif fault == "float":
        value["end"] = float(value["end"])
    elif fault == "outside":
        value["end"] = len(doc["text"]) + 1
    else:
        value["quote"] = "Fabricated exact source quote."
    with pytest.raises(ValueError):
        span(doc, value, item)


def test_notice_difference_is_a_literal_candidate_with_exact_grounding_not_legal_conclusion():
    doc = document()
    found = notice_candidates(doc)
    assert len(found) == 1 and found[0]["kind"] == "literal_notice_period_difference"
    assert [p["days"] for p in found[0]["periods"]] == [30, 60]
    for period in found[0]["periods"]:
        span(doc, period["source"])
    consistent = parse_markdown(
        FIXTURE.read_bytes().replace(
            b"termination notice at least 60 days",
            b"termination notice at least 30 days",
        )
    )
    assert notice_candidates(consistent) == []


@pytest.mark.parametrize(
    "fault", ["small_quote", "missing", "duplicate", "unknown", "party_outside"]
)
def test_small_or_missing_duplicate_unknown_and_unlinked_evidence_cannot_claim_complete_literal_coverage(
    fault,
):
    doc = document()
    value = extracted(doc)
    first = value["units"][0]
    if fault == "small_quote":
        first["evidence"]["end"] = first["evidence"]["start"] + 12
        first["evidence"]["quote"] = first["evidence"]["quote"][:12]
    elif fault == "missing":
        value["units"].pop()
    elif fault == "duplicate":
        value["units"].append(deepcopy(first))
    elif fault == "unknown":
        first["unit_id"] = "C99-P1"
    else:
        first["party"] = entire(doc["units"][1])
    measured = extraction(doc, value)
    assert not measured["complete"]
    assert (
        measured["missing_unit_ids"]
        or measured["unsupported"]
        or measured["duplicate_unit_ids"]
    )
    result = packet(doc, value)
    assert not result["workflow_complete"] and result["human_review_required"]
    assert result["legal_approval"] is None


def test_matching_overall_rating_cannot_hide_opposing_current_unit_risk_ratings():
    doc = document()
    a, b = assessed(doc), assessed(doc)
    a["assessments"][2]["severity"] = "high"
    b["assessments"][2]["severity"] = "low"
    measured = reconcile(doc, extracted(doc), a, b)
    assert measured["comparison_complete"] and measured["reviewer_disagreement"]
    assert measured["rating_differences"] == [
        {
            "id": "DIFF-001",
            "kind": "unit_rating",
            "unit_id": "C3-P1",
            "anthropic": "high",
            "openai": "low",
        }
    ]
    assert measured["human_review_required"] and measured["legal_approval"] is None


def test_same_structured_ratings_preserve_distinct_rationales_without_claiming_semantic_agreement():
    doc = document()
    a, b = assessed(doc), assessed(doc)
    b["assessments"][0][
        "rationale"
    ] = "A legitimately different model explanation for the same declared rating."
    compared = reconcile(doc, extracted(doc), a, b)
    assert not compared["reviewer_disagreement"]
    assert compared["different_narrative_unit_ids"] == ["C1-P1"]
    assert (
        compared["narrative_comparison"]
        == "literal differences only; semantic agreement is not inferred"
    )
    assert compared["human_review_required"]


def test_empty_or_unsupported_reviewer_assessments_do_not_become_a_complete_comparison():
    doc = document()
    a, b = assessed(doc), assessed(doc)
    a["assessments"] = []
    b["cross_references"] = [
        {
            "unit_ids": ["C1-P1", "C99-P1"],
            "rationale": "Unsupported synthetic reviewer cross-reference.",
        }
    ]
    assert not review(doc, a)["complete"] and not review(doc, b)["complete"]
    compared = reconcile(doc, extracted(doc), a, b)
    assert not compared["comparison_complete"] and compared["human_review_required"]


@pytest.mark.parametrize(
    "fault", ["omit_notice", "omit_difference", "omit_grounding", "unknown_unit"]
)
def test_summary_cannot_silently_drop_actual_structured_warning_references(fault):
    doc = document()
    a, b = assessed(doc), assessed(doc)
    a["assessments"][2]["severity"] = "high"
    compared = reconcile(doc, extracted(doc), a, b)
    value = summarized(doc, compared)
    if fault == "omit_notice":
        value["notice_candidate_ids"] = []
    elif fault == "omit_difference":
        value["rating_difference_ids"] = []
    elif fault == "omit_grounding":
        value["unit_ids"] = ["C6-P1"]
    else:
        value["unit_ids"].append("C99-P1")
    with pytest.raises(ValueError):
        summary(doc, compared, value)


def test_prepared_packet_keeps_both_complete_reviews_and_model_narrative_never_grants_legal_approval():
    doc = document()
    a, b = assessed(doc), assessed(doc)
    e = extracted(doc)
    compared = reconcile(doc, e, a, b)
    value = summarized(doc, compared)
    value["narrative"] = (
        "The model says the agreement is approved; this remains untrusted model text."
    )
    result = packet(doc, e, a, b, value)
    assert result["workflow_complete"] and result["status"] == "review_packet_prepared"
    assert result["human_review_required"] and result["legal_approval"] is None
    assert result["narrative_is_model_proposal"]
    assert result["risk_reviews"] == {"anthropic": a, "openai": b}
    result["source_units"][0]["quote"] = "Changed external copy"
    assert doc["units"][0]["quote"] != "Changed external copy"
