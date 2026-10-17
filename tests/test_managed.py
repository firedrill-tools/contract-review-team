"""Actual SQLite/current caller/read/ordered review control; human-only backend role seam."""

import base64
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from contract_review.domain import HUMAN_ROLE, document, make_document
from contract_review.managed import Credential, ManagedSource
from contract_review.strict import canonical
from test_policy import extracted, assessed, summarized
from contract_review.policy import reconcile

MARKDOWN = Path(__file__).parents[1] / "fixtures/meridian-services.md"


def seed(tmp_path):
    row = make_document("D-701", MARKDOWN.read_bytes())
    return ManagedSource.seed_bytes(
        tmp_path / "review.db",
        json.dumps({"documents": [row], "reviews": [], "audit": []}).encode(),
    )


def scope(source, writes=True):
    return source.begin(
        source.intent(
            "D-701", "Prepare an exact source-bound synthetic review packet."
        ),
        "Explicit human-only backend role data, no model execution claim.",
        allow_writes=writes,
        execution="human",
    )


def apply(source, cap, stage, payload=None, read=None):
    read = source.observe(cap, stage) if read is None else read
    value = read["value"]
    action = {
        "kind": "stage",
        "args": {
            "review_id": value["review_id"],
            "document": deepcopy(value["document"]),
            "stage": stage,
            "payload": deepcopy(payload),
            "provenance": (
                deepcopy(HUMAN_ROLE)
                if stage in ("extraction", "risk_anthropic", "risk_openai", "summary")
                else None
            ),
        },
    }
    return source.commit(cap, action, read["observation"]["id"], read["proof"])


def finish(source, cap):
    doc = document(source.snapshot()["business"]["documents"][0])
    e = extracted(doc)
    a, b = assessed(doc), assessed(doc)
    b["assessments"][2]["severity"] = "high"
    compared = reconcile(doc, e, a, b)
    for stage, payload in [
        ("extraction", e),
        ("risk_anthropic", a),
        ("risk_openai", b),
        ("reconcile", None),
        ("summary", summarized(doc, compared)),
        ("publish", None),
    ]:
        apply(source, cap, stage, payload)


def test_actual_managed_current_read_ordered_roles_atomic_packet_and_real_resource_close(
    tmp_path,
):
    source = seed(tmp_path)
    cap = scope(source)
    with source:
        finish(source, cap)
        final = source.snapshot()
        assert (
            len(final["records"]["decisions"]) == len(final["business"]["audit"]) == 6
        )
        assert [d["status"] for d in final["records"]["decisions"]] == ["applied"] * 6
        row = final["business"]["reviews"][0]
        assert row == final["views"][cap.id] and row["packet"]["workflow_complete"]
        assert (
            row["packet"]["legal_approval"] is None
            and row["packet"]["human_review_required"]
        )
        source.close_operation(cap)
        assert source.close_operation(cap)["already_closed"]
        with pytest.raises(ValueError):
            source.observe(cap, "extraction")
    assert source.database_closed and source.descriptor_closed
    with ManagedSource(source.path) as fresh:
        assert fresh.snapshot()["business"] == final["business"]


def test_readonly_private_role_progress_and_preview_do_not_mutate_literal_business(
    tmp_path,
):
    with seed(tmp_path) as source:
        before = source.database.execute("SELECT document FROM business").fetchone()[0]
        cap = scope(source, False)
        finish(source, cap)
        final = source.snapshot()
        assert final["views"][cap.id]["packet"]["workflow_complete"]
        assert [d["status"] for d in final["records"]["decisions"]] == ["denied"] * 6
        assert (
            source.database.execute("SELECT document FROM business").fetchone()[0]
            == before
        )
        assert final["business"]["reviews"] == final["business"]["audit"] == []
        source.close_operation(cap)


def test_real_document_replacement_invalidates_pending_role_and_cannot_refresh_old_scope(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        doc = document(source.snapshot()["business"]["documents"][0])
        apply(source, cap, "extraction", extracted(doc))
        read = source.observe(cap, "risk_anthropic")
        with ManagedSource(source.path) as human:
            human.replace_document(
                "D-701",
                MARKDOWN.read_bytes().replace(b"within 72 hours", b"within 720 hours"),
            )
        with pytest.raises(ValueError):
            apply(source, cap, "risk_anthropic", assessed(doc), read)
        with pytest.raises(ValueError, match="document_changed"):
            source.observe(cap, "risk_anthropic")
        final = source.snapshot()
        assert len(final["business"]["reviews"][0]["observations"]) == 1
        assert (
            final["business"]["reviews"][0]["document"]["revision"] == 1
            and final["business"]["documents"][0]["revision"] == 2
        )
        source.close_operation(cap)


def test_incomplete_extraction_can_only_publish_its_explicit_incomplete_packet(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        doc = document(source.snapshot()["business"]["documents"][0])
        e = extracted(doc)
        e["units"].pop()
        apply(source, cap, "extraction", e)
        with pytest.raises(ValueError):
            source.observe(cap, "risk_anthropic")
        assert apply(source, cap, "publish")["result"]["workflow_complete"] is False
        final = source.snapshot()
        assert len(final["business"]["reviews"][0]["observations"]) == 1
        source.close_operation(cap)


@pytest.mark.parametrize(
    "fault", ["guessed", "foreign", "stage", "target", "document", "consumed", "closed"]
)
def test_genuine_own_current_unused_read_and_exact_target_stage_are_required(
    tmp_path, fault
):
    with seed(tmp_path) as source:
        cap = scope(source)
        doc = document(source.snapshot()["business"]["documents"][0])
        read = source.observe(cap, "extraction")
        args = {
            "review_id": read["value"]["review_id"],
            "document": deepcopy(read["value"]["document"]),
            "stage": "extraction",
            "payload": extracted(doc),
            "provenance": HUMAN_ROLE,
        }
        proof = read["proof"]
        credential = cap
        if fault == "guessed":
            proof = "guessed"
        elif fault == "foreign":
            credential = Credential(cap.id, "wrong", cap.authority_sha256)
        elif fault == "stage":
            args["stage"] = "risk_anthropic"
            args["payload"] = assessed(doc)
        elif fault == "target":
            args["review_id"] = "R-FOREIGN"
        elif fault == "document":
            args["document"]["revision"] = 2
        elif fault == "consumed":
            source.commit(
                cap, {"kind": "stage", "args": args}, read["observation"]["id"], proof
            )
        else:
            source.close_operation(cap)
        before = source.snapshot()["sha256"]
        with pytest.raises(ValueError):
            source.commit(
                credential,
                {"kind": "stage", "args": args},
                read["observation"]["id"],
                proof,
            )
        assert source.snapshot()["sha256"] == before
        source.close_operation(cap)


def test_native_scope_requires_original_model_plan_and_human_seam_cannot_claim_native(
    tmp_path,
):
    with seed(tmp_path) as source:
        intent = source.intent("D-701", "Prepare a review.")
        with pytest.raises(ValueError):
            source.begin(intent, "No configured native models")
        cap = scope(source)
        read = source.observe(cap, "extraction")
        doc = document(read["value"]["document"])
        args = {
            "review_id": read["value"]["review_id"],
            "document": read["value"]["document"],
            "stage": "extraction",
            "payload": extracted(doc),
            "provenance": {
                "kind": "owned_native_role",
                "provider": "anthropic",
                "model": "synthetic-model",
                "request_sha256": "1" * 64,
                "response_sha256": "2" * 64,
                "legal_approval": None,
            },
        }
        with pytest.raises(ValueError, match="human"):
            source.commit(
                cap,
                {"kind": "stage", "args": args},
                read["observation"]["id"],
                read["proof"],
            )
        source.close_operation(cap)


def test_self_consistent_sql_permission_promotion_cannot_grant_live_scope_new_rights(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source, False)
        value = json.loads(
            source.database.execute("SELECT document FROM operations").fetchone()[0]
        )
        value["allow_writes"] = True
        source.database.execute("UPDATE operations SET document=?", (canonical(value),))
        assert source.snapshot()["records"]["operations"][0]["allow_writes"]
        with pytest.raises(ValueError):
            source.observe(cap, "extraction")
        assert source.close_operation(cap)["closed"]


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "header",
        "business",
        "duplicate_sql_json",
        "read",
        "decision",
        "closure_cutoff",
    ],
)
def test_every_original_sql_schema_header_role_read_and_decision_replays_without_repair(
    tmp_path, fault
):
    source = seed(tmp_path)
    cap = scope(source)
    finish(source, cap)
    source.close_operation(cap)
    source.close()
    with sqlite3.connect(source.path) as db:
        if fault == "schema":
            db.execute("CREATE TABLE extra(x)")
        elif fault == "duplicate_sql_json":
            db.execute(
                "UPDATE business SET document=?",
                ('{"documents":[],"reviews":[],"audit":[],"audit":[]}',),
            )
        else:
            table = (
                "metadata"
                if fault == "header"
                else (
                    "business"
                    if fault == "business"
                    else (
                        "observations"
                        if fault == "read"
                        else "decisions" if fault == "decision" else "operations"
                    )
                )
            )
            identity, raw = db.execute(
                "SELECT id,document FROM " + table + " LIMIT 1"
            ).fetchone()
            value = json.loads(raw)
            if fault == "header":
                value["fixture_sha256"] = "0" * 64
            elif fault == "business":
                value["reviews"][0]["packet"]["legal_approval"] = "invented approval"
            elif fault == "read":
                value["stage"] = "publish"
            elif fault == "decision":
                value["result"]["stage"] = "publish"
            else:
                value["closed_frontier"] = 0
            db.execute(
                "UPDATE " + table + " SET document=? WHERE id=?",
                (canonical(value), identity),
            )
    before = source.path.read_bytes()
    with pytest.raises(ValueError):
        ManagedSource(source.path)
    assert source.path.read_bytes() == before


def test_cancelled_precommit_rolls_back_and_control_revocation_survives_damaged_business(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        read = source.observe(cap, "extraction")
        doc = document(read["value"]["document"])
        before = source.snapshot()["sha256"]

        def cancelled():
            raise TimeoutError("actual application commit guard")

        source.commit_guard = cancelled
        with pytest.raises(TimeoutError):
            apply(source, cap, "extraction", extracted(doc), read)
        assert source.snapshot()["sha256"] == before
        source.database.execute("UPDATE business SET document='{}'")
        with pytest.raises(ValueError):
            source.snapshot()
        assert source.close_operation(cap)["closed"]
        assert (
            json.loads(
                source.database.execute("SELECT document FROM operations").fetchone()[0]
            )["status"]
            == "closed"
        )


def test_real_writer_lock_competing_owner_and_absent_read_never_create_or_replace_source(
    tmp_path,
):
    with seed(tmp_path) as source:
        with sqlite3.connect(source.path, isolation_level=None) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.OperationalError):
                scope(source)
            writer.execute("ROLLBACK")
        assert source.snapshot()["records"]["operations"] == []
        cap = scope(source)
        with ManagedSource(source.path) as other:
            with pytest.raises(ValueError, match="active"):
                scope(other)
        source.close_operation(cap)
    absent = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        ManagedSource(absent)
    assert not absent.exists()


def test_actual_database_close_is_attempted_once_and_failed_acknowledgement_stays_unconfirmed(
    tmp_path,
):
    source = seed(tmp_path)
    actual = source.database

    class ClosedThenFailed:
        calls = 0

        def __getattr__(self, name):
            return getattr(actual, name)

        def close(self):
            self.calls += 1
            actual.close()
            raise RuntimeError(
                "close acknowledgement lost after actual connection close"
            )

    wrapper = ClosedThenFailed()
    source.database = wrapper
    result = source.close()
    source.close()
    assert (
        not result["database_closed"]
        and result["descriptor_closed"]
        and wrapper.calls == 1
    )
    with pytest.raises(sqlite3.ProgrammingError):
        actual.execute("SELECT 1")


def test_private_real_cli_import_inspect_intent_replace_outside_checkout_is_model_free(
    tmp_path,
):
    import subprocess, sys

    def cli(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "contract_review.managed_cli",
                *map(str, args),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    database = tmp_path / "private.db"
    initial = cli(
        "init", "--db", database, "--contract", MARKDOWN, "--document-id", "D-701"
    )
    assert initial["records"] == {"operations": [], "observations": [], "decisions": []}
    intent = cli(
        "intent",
        "--db",
        database,
        "--document-id",
        "D-701",
        "--request",
        "Review exact current source.",
    )
    assert (
        intent["document"]["markdown_sha256"]
        == initial["business"]["documents"][0]["markdown_sha256"]
    )
    revised = tmp_path / "revised.md"
    revised.write_bytes(
        MARKDOWN.read_bytes().replace(b"within 72 hours", b"within 720 hours")
    )
    result = cli(
        "replace", "--db", database, "--document-id", "D-701", "--contract", revised
    )
    assert result["result"]["revision"] == 2
    final = cli("inspect", "--db", database)
    assert final["source"]["instance_id"] == initial["source"]["instance_id"]
    assert final["records"]["operations"] == final["records"]["observations"] == []
    assert (
        final["business"]["reviews"] == []
        and final["business"]["documents"][0]["revision"] == 2
    )
