"""Complete human contract/review history and real retained source file owner."""

import base64
import json
from copy import deepcopy
from pathlib import Path

import pytest

from contract_review.domain import HUMAN_ROLE, make_document
from contract_review.human import HumanWorkspace
from contract_review.source import parse
from contract_review.strict import MAX_INTEGER
from test_policy import extracted, assessed, summarized
from contract_review.domain import document
from contract_review.policy import reconcile

MARKDOWN = Path(__file__).parents[1] / "fixtures/meridian-services.md"


def seed(tmp_path, revision=1):
    path = tmp_path / "workspace.json"
    value = {
        "documents": [make_document("D-701", MARKDOWN.read_bytes(), revision)],
        "reviews": [],
        "audit": [],
    }
    if revision != 1:
        value["baselineVersion"] = 1
    path.write_text(json.dumps(value))
    return path, HumanWorkspace(path)


def stage(owner, role, payload=None, source=None):
    row = owner.inspect()["business"]["documents"][0] if source is None else source
    return {
        "kind": "stage",
        "args": {
            "review_id": "R-HUMAN-001",
            "document": deepcopy(row),
            "stage": role,
            "payload": deepcopy(payload),
            "provenance": (
                deepcopy(HUMAN_ROLE)
                if role in ("extraction", "risk_anthropic", "risk_openai", "summary")
                else None
            ),
        },
    }


def complete(owner):
    doc = document(owner.inspect()["business"]["documents"][0])
    e = extracted(doc)
    a, b = assessed(doc), assessed(doc)
    b["assessments"][2]["severity"] = "high"
    compared = reconcile(doc, e, a, b)
    for role, payload in [
        ("extraction", e),
        ("risk_anthropic", a),
        ("risk_openai", b),
        ("reconcile", None),
        ("summary", summarized(doc, compared)),
        ("publish", None),
    ]:
        owner.apply(stage(owner, role, payload))


def test_complete_human_role_reconciliation_packet_history_and_exact_original_markdown(
    tmp_path,
):
    original = MARKDOWN.read_bytes()
    path, owner = seed(tmp_path)
    with owner:
        complete(owner)
        current = owner.inspect()
        assert len(current["history"]) == len(current["business"]["audit"]) == 6
        row = current["business"]["reviews"][0]
        assert row["revision"] == 6 and row["packet"]["workflow_complete"]
        assert (
            row["packet"]["human_review_required"]
            and row["packet"]["legal_approval"] is None
        )
        assert row["reconciliation"]["rating_differences"][0]["unit_id"] == "C3-P1"
        assert [o["role"] for o in row["observations"]] == [
            "extraction",
            "risk_anthropic",
            "risk_openai",
            "summary",
        ]
        assert all(o["provenance"] == HUMAN_ROLE for o in row["observations"])
        assert parse(path.read_bytes()) == current
    assert owner.closed and owner.fd is None and MARKDOWN.read_bytes() == original


def test_human_document_revision_retains_superseded_report_and_invalidates_old_stage(
    tmp_path,
):
    path, owner = seed(tmp_path)
    with owner:
        old = owner.inspect()["business"]["documents"][0]
        doc = document(old)
        owner.apply(stage(owner, "extraction", extracted(doc)))
        raw = MARKDOWN.read_bytes().replace(b"within 72 hours", b"within 720 hours")
        action = {
            "kind": "replace_document",
            "args": {
                "document_id": "D-701",
                "source": old,
                "markdown_base64": base64.b64encode(raw).decode(),
            },
        }
        assert owner.apply(action)["revision"] == 2
        before = path.read_bytes()
        assert (
            owner.apply(stage(owner, "risk_anthropic", assessed(doc), old))["status"]
            == "document_changed"
        )
        assert path.read_bytes() == before
        current = owner.inspect()["business"]
        assert current["reviews"][0]["document"] == old
        assert current["documents"][0]["markdown_sha256"] != old["markdown_sha256"]
        assert current["reviews"][0]["packet"] is None


def test_incomplete_literal_extraction_publishes_incomplete_packet_without_risk_or_summary(
    tmp_path,
):
    path, owner = seed(tmp_path)
    with owner:
        doc = document(owner.inspect()["business"]["documents"][0])
        e = extracted(doc)
        e["units"].pop()
        owner.apply(stage(owner, "extraction", e))
        before = path.read_bytes()
        with pytest.raises(ValueError, match="ordered"):
            owner.apply(stage(owner, "risk_anthropic", assessed(doc)))
        assert path.read_bytes() == before
        owner.apply(stage(owner, "publish"))
        row = owner.inspect()["business"]["reviews"][0]
        assert len(row["observations"]) == 1 and row["revision"] == 2
        assert (
            not row["packet"]["workflow_complete"]
            and row["packet"]["human_review_required"]
        )
        before = path.read_bytes()
        assert owner.apply(stage(owner, "publish"))["status"] == "already_published"
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "review_before_extract",
        "summary_before_reconcile",
        "publish_before_reviews",
        "model_reconcile",
    ],
)
def test_ordered_role_and_computed_stage_lineage_cannot_be_asserted_by_model_data(
    tmp_path, fault
):
    path, owner = seed(tmp_path)
    with owner:
        doc = document(owner.inspect()["business"]["documents"][0])
        if fault != "review_before_extract":
            owner.apply(stage(owner, "extraction", extracted(doc)))
        if fault in ("summary_before_reconcile", "model_reconcile"):
            owner.apply(stage(owner, "risk_anthropic", assessed(doc)))
            owner.apply(stage(owner, "risk_openai", assessed(doc)))
        action = (
            stage(owner, "risk_anthropic", assessed(doc))
            if fault == "review_before_extract"
            else (
                stage(
                    owner,
                    "summary",
                    {"narrative": "A fabricated stage without prior reconciliation."},
                )
                if fault == "summary_before_reconcile"
                else (
                    stage(owner, "publish")
                    if fault == "publish_before_reviews"
                    else stage(owner, "reconcile", {"reviewer_disagreement": False})
                )
            )
        )
        before = path.read_bytes()
        with pytest.raises(ValueError):
            owner.apply(action)
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "business",
        "history",
        "role_payload",
        "validation",
        "packet",
        "provenance",
        "origin",
    ],
)
def test_complete_original_document_role_packet_and_provenance_history_replay_refuses_without_repair(
    tmp_path, fault
):
    path, owner = seed(tmp_path)
    with owner:
        complete(owner)
        value = owner.inspect()
    if fault == "business":
        value["business"]["documents"][0]["title"] = "Unsupported current title"
    elif fault == "history":
        value["history"].pop(0)
    elif fault == "role_payload":
        value["history"][1]["action"]["args"]["payload"]["overall"] = "low"
    elif fault == "validation":
        value["business"]["reviews"][0]["observations"][0]["validation"][
            "complete"
        ] = False
    elif fault == "packet":
        value["business"]["reviews"][0]["packet"]["legal_approval"] = "approved"
    elif fault == "provenance":
        value["business"]["reviews"][0]["observations"][0]["provenance"][
            "model"
        ] = "invented prior model"
    else:
        value["origin"]["provenance"]["prior_model"] = "invented prior model"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        HumanWorkspace(path)
    assert path.read_bytes() == raw


def test_human_fixture_cannot_import_owned_native_role_even_with_plausible_response_hashes(
    tmp_path,
):
    path, owner = seed(tmp_path)
    with owner:
        doc = document(owner.inspect()["business"]["documents"][0])
        action = stage(owner, "extraction", extracted(doc))
        action["args"]["provenance"] = {
            "kind": "owned_native_role",
            "provider": "anthropic",
            "model": "synthetic-model",
            "request_sha256": "1" * 64,
            "response_sha256": "2" * 64,
            "legal_approval": None,
        }
        before = path.read_bytes()
        with pytest.raises(ValueError, match="provenance"):
            owner.apply(action)
        assert path.read_bytes() == before


def test_initial_fixture_cannot_import_fabricated_prior_role_records_or_audit(tmp_path):
    path, owner = seed(tmp_path)
    with owner:
        complete(owner)
        current = owner.inspect()["business"]
    path.write_text(json.dumps(current))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="cannot import"):
        HumanWorkspace(path)
    assert path.read_bytes() == before


def test_last_safe_document_revision_preserves_completed_review_on_exhausted_replacement(
    tmp_path,
):
    path, owner = seed(tmp_path, MAX_INTEGER - 1)
    with owner:
        complete(owner)
        original = owner.inspect()["business"]["documents"][0]
        raw = MARKDOWN.read_bytes().replace(b"within 72 hours", b"within 73 hours")
        a = {
            "kind": "replace_document",
            "args": {
                "document_id": "D-701",
                "source": original,
                "markdown_base64": base64.b64encode(raw).decode(),
            },
        }
        assert owner.apply(a)["revision"] == MAX_INTEGER
        row = owner.inspect()["business"]["documents"][0]
        a["args"]["source"] = row
        a["args"]["markdown_base64"] = base64.b64encode(MARKDOWN.read_bytes()).decode()
        before = path.read_bytes()
        with pytest.raises(ValueError, match="revision_exhausted"):
            owner.apply(a)
        assert (
            path.read_bytes() == before
            and owner.inspect()["business"]["reviews"][0]["packet"]["workflow_complete"]
        )


def test_original_source_alias_and_replacement_missing_refuse_and_owner_closes(
    tmp_path,
):
    path, owner = seed(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        HumanWorkspace(alias)
    other = tmp_path / "other.json"
    other.write_bytes(path.read_bytes())
    other.replace(path)
    with pytest.raises(ValueError):
        owner.inspect()
    owner.close()
    owner.close()
    assert owner.closed and owner.fd is None
    path.unlink()
    with pytest.raises(FileNotFoundError):
        HumanWorkspace(path)
    assert not path.exists()
