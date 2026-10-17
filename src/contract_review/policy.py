"""Literal source coverage and visible reviewer differences; no legal approval authority."""

from copy import deepcopy

from .document import entire, notice_candidates, span, unit
from .strict import digest, keys, text

RATINGS = ("low", "medium", "high", "unknown")


def rating(value):
    if type(value) is not str or value not in RATINGS:
        raise ValueError("exact proposed review rating required")
    return value


def listed(value, maximum=128):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("bounded original review list required")
    return value


def extraction(document, value):
    keys(value, {"units"})
    covered, duplicates, unsupported = set(), [], []
    for index, item in enumerate(listed(value["units"])):
        keys(
            item, {"unit_id", "evidence", "party", "action", "deadline", "uncertainty"}
        )
        text(item["unit_id"], 1, 64)
        text(item["uncertainty"], 0, 1000)
        try:
            source = unit(document, item["unit_id"])
            span(document, item["evidence"], source)
            if item["evidence"] != entire(source):
                raise ValueError("complete literal source unit evidence required")
            span(document, item["action"], source)
            for field in ("party", "deadline"):
                if item[field] is not None:
                    span(document, item[field], source)
            if item["unit_id"] in covered:
                duplicates.append(item["unit_id"])
            covered.add(item["unit_id"])
        except ValueError as error:
            unsupported.append(
                {"index": index, "unit_id": item["unit_id"], "reason": str(error)}
            )
    missing = [item["id"] for item in document["units"] if item["id"] not in covered]
    return {
        "complete": not missing and not unsupported and not duplicates,
        "missing_unit_ids": missing,
        "unsupported": unsupported,
        "duplicate_unit_ids": sorted(set(duplicates)),
        "coverage_kind": "literal body paragraph units; headings retained separately; semantic and legal completeness unknown",
    }


def review(document, value):
    keys(value, {"overall", "assessments", "cross_references"})
    rating(value["overall"])
    covered, duplicates, unsupported = set(), [], []
    for index, item in enumerate(listed(value["assessments"])):
        keys(item, {"unit_id", "evidence", "severity", "rationale", "uncertainty"})
        text(item["unit_id"], 1, 64)
        rating(item["severity"])
        text(item["rationale"], 8, 2000)
        text(item["uncertainty"], 0, 1000)
        try:
            source = unit(document, item["unit_id"])
            span(document, item["evidence"], source)
            if item["evidence"] != entire(source):
                raise ValueError("complete literal review unit evidence required")
            if item["unit_id"] in covered:
                duplicates.append(item["unit_id"])
            covered.add(item["unit_id"])
        except ValueError as error:
            unsupported.append(
                {"index": index, "unit_id": item["unit_id"], "reason": str(error)}
            )
    for index, item in enumerate(listed(value["cross_references"], 64)):
        keys(item, {"unit_ids", "rationale"})
        ids = listed(item["unit_ids"], 8)
        text(item["rationale"], 8, 2000)
        if (
            len(ids) < 2
            or any(type(ident) is not str for ident in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("unique bounded explicit cross-reference units required")
        for ident in ids:
            try:
                unit(document, ident)
            except ValueError as error:
                unsupported.append(
                    {
                        "cross_reference_index": index,
                        "unit_id": ident,
                        "reason": str(error),
                    }
                )
    missing = [item["id"] for item in document["units"] if item["id"] not in covered]
    return {
        "complete": not missing and not unsupported and not duplicates,
        "missing_unit_ids": missing,
        "unsupported": unsupported,
        "duplicate_unit_ids": sorted(set(duplicates)),
        "interpretation": "source-grounded model assessments; legal correctness unknown",
    }


def reconcile(document, extracted, anthropic, openai):
    extraction_validation = extraction(document, extracted)
    validations = {
        "anthropic": review(document, anthropic),
        "openai": review(document, openai),
    }
    left = {item["unit_id"]: item for item in anthropic["assessments"]}
    right = {item["unit_id"]: item for item in openai["assessments"]}
    differences = []
    if anthropic["overall"] != openai["overall"]:
        differences.append(
            {
                "kind": "overall_rating",
                "anthropic": anthropic["overall"],
                "openai": openai["overall"],
            }
        )
    narratives = []
    for source in document["units"]:
        ident = source["id"]
        a, b = left.get(ident), right.get(ident)
        if a is not None and b is not None:
            if a["severity"] != b["severity"]:
                differences.append(
                    {
                        "kind": "unit_rating",
                        "unit_id": ident,
                        "anthropic": a["severity"],
                        "openai": b["severity"],
                    }
                )
            if a["rationale"] != b["rationale"] or a["uncertainty"] != b["uncertainty"]:
                narratives.append(ident)
    cross_left = {
        tuple(sorted(item["unit_ids"])) for item in anthropic["cross_references"]
    }
    cross_right = {
        tuple(sorted(item["unit_ids"])) for item in openai["cross_references"]
    }
    cross_differences = {
        "anthropic_only": [list(group) for group in sorted(cross_left - cross_right)],
        "openai_only": [list(group) for group in sorted(cross_right - cross_left)],
    }
    differences = [
        {"id": f"DIFF-{i:03}", **item} for i, item in enumerate(differences, 1)
    ]
    return {
        "source_sha256": document["sha256"],
        "extraction_validation": extraction_validation,
        "review_validations": validations,
        "comparison_complete": extraction_validation["complete"]
        and all(v["complete"] for v in validations.values()),
        "notice_candidates": notice_candidates(document),
        "reviewer_disagreement": bool(
            differences
            or cross_differences["anthropic_only"]
            or cross_differences["openai_only"]
        ),
        "rating_differences": differences,
        "cross_reference_differences": cross_differences,
        "different_narrative_unit_ids": narratives,
        "narrative_comparison": "literal differences only; semantic agreement is not inferred",
        "human_review_required": True,
        "legal_approval": None,
    }


def summary(document, reconciliation, value):
    keys(
        value,
        {"narrative", "unit_ids", "notice_candidate_ids", "rating_difference_ids"},
    )
    text(value["narrative"], 20, 12000)
    for field in ("unit_ids", "notice_candidate_ids", "rating_difference_ids"):
        ids = listed(value[field])
        if any(type(ident) is not str for ident in ids) or len(set(ids)) != len(ids):
            raise ValueError("exact unique summary references required")
    if not value["unit_ids"]:
        raise ValueError("source-referenced model summary required")
    for ident in value["unit_ids"]:
        unit(document, ident)
    notices = {item["id"] for item in reconciliation["notice_candidates"]}
    ratings = {item["id"] for item in reconciliation["rating_differences"]}
    if (
        set(value["notice_candidate_ids"]) != notices
        or set(value["rating_difference_ids"]) != ratings
    ):
        raise ValueError(
            "summary must retain every actual notice candidate/rating difference"
        )
    required_units = {
        period["unit_id"]
        for candidate in reconciliation["notice_candidates"]
        for period in candidate["periods"]
    }
    required_units.update(
        item["unit_id"]
        for item in reconciliation["rating_differences"]
        if "unit_id" in item
    )
    if not required_units <= set(value["unit_ids"]):
        raise ValueError(
            "summary citations omit current source of a declared difference"
        )
    return {
        "complete": True,
        "proposal_sha256": digest(value),
        "narrative_is_model_proposal": True,
        "human_review_required": True,
        "legal_approval": None,
    }


def packet(document, extracted, anthropic=None, openai=None, summary_value=None):
    validation = extraction(document, extracted)
    agreement = (
        reconcile(document, extracted, anthropic, openai)
        if anthropic is not None and openai is not None
        else None
    )
    summary_validation = (
        summary(document, agreement, summary_value)
        if agreement is not None
        and agreement["comparison_complete"]
        and summary_value is not None
        else None
    )
    complete = (
        validation["complete"]
        and agreement is not None
        and agreement["comparison_complete"]
        and summary_validation is not None
    )
    return {
        "status": "review_packet_prepared" if complete else "incomplete_review_packet",
        "workflow_complete": complete,
        "source_sha256": document["sha256"],
        "source_title": document["title"],
        "source_text": document["text"],
        "source_clauses": deepcopy(document["clauses"]),
        "source_unit_definition": "body paragraphs; complete original headings and text are retained separately",
        "source_units": deepcopy(document["units"]),
        "extraction": deepcopy(extracted),
        "extraction_validation": validation,
        "risk_reviews": {"anthropic": deepcopy(anthropic), "openai": deepcopy(openai)},
        "reconciliation": agreement,
        "summary": deepcopy(summary_value),
        "summary_validation": summary_validation,
        "narrative_is_model_proposal": True,
        "human_review_required": True,
        "legal_approval": None,
        "scope": "literal source and structured model review packet; no legal correctness or semantic completeness claim",
    }
