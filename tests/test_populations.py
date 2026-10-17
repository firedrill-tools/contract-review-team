"""Twelve private pinned scenarios, actual normal SDK paths, preserving resets."""

import asyncio
import base64
import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from contract_review.cases import initialize, inspect_case, reset_case
from contract_review.domain import ROLES
from contract_review.managed import ManagedSource
from contract_review.owned_runtime import run_owned_review
from contract_review.populations import dataset, definition, sealed
from contract_review.strict import canonical, parse_json, sha256
from test_native import provider, receipt


@pytest.mark.parametrize("case_id", [c["id"] for c in dataset()["cases"]])
def test_all_twelve_actual_native_cases_keep_private_oracle_off_wire_and_reset_preserves_old_source(
    tmp_path, case_id
):
    case = definition(case_id)
    info = initialize(tmp_path / "old", case_id)
    assert info["private_reference"] == case["reference"]
    initial_business = deepcopy(info["source"]["business"])
    changed = False

    def before(role, captured):
        nonlocal changed
        if (
            case["during"] is not None
            and role == case["during"]["stage"]
            and not changed
        ):
            with ManagedSource(info["database"]) as source:
                source.replace_document(
                    case["during"]["document_id"],
                    base64.b64decode(case["during"]["markdown_base64"]),
                )
            changed = True

    def mutate(role, response, captured):
        fault = case["native_fault"]
        if fault is None or fault["role"] != role:
            return
        if fault["kind"] == "missing_unit":
            payload = response["content"][0]["input"]
            payload["units" if role == "extraction" else "assessments"].pop()
        elif fault["kind"] == "null_finish":
            response["choices"][0]["finish_reason"] = None
        elif fault["kind"] == "drop_warnings":
            payload = json.loads(response["choices"][0]["message"]["content"])
            payload["notice_candidate_ids"] = payload["rating_difference_ids"] = []
            response["choices"][0]["message"]["content"] = json.dumps(payload)

    results, evidences = [], []
    with provider(mutate=mutate, before=before) as (base, captured):
        for index, caller in enumerate(case["runs"]):
            opts = {
                "database": info["database"],
                **caller,
                "anthropic_key": "owned-anthropic-key",
                "openai_key": "owned-openai-key",
                "models": {r: "owned-" + r for r in ROLES},
                "anthropic_base": base,
                "openai_base": base,
                "output_root": tmp_path / "old" / "receipts",
                "timeout_ms": 120000,
            }
            if case["reference"]["technical_failure"]:
                with pytest.raises(Exception) as exc:
                    asyncio.run(run_owned_review(**opts))
                evidences.append(exc.value.evidence)
            else:
                result = asyncio.run(run_owned_review(**opts))
                results.append(result)
                evidences.append(receipt(result))
    assert len(captured) == case["reference"]["expected_calls"]
    assert case["reference"]["private_canary"] not in json.dumps(captured)
    assert all(
        "private_reference" not in c["public"] and "native_fault" not in c["public"]
        for c in captured
    )
    assert all(ev["cleanup_confirmed"] for ev in evidences)
    if results:
        for result in results:
            assert (
                result["business"]["report_saved"] == case["reference"]["report_saved"]
            )
            assert result["packet"]["workflow_complete"] == (
                case["reference"]["packet_complete"]
            )
            assert (
                result["packet"]["human_review_required"]
                and result["packet"]["legal_approval"] is None
            )
    with ManagedSource(info["database"]) as source:
        before = source.snapshot()
        if case_id == "starter-03":
            assert before["business"] == initial_business
        if case["profile"] == "busy":
            assert (
                len(initial_business["documents"]) == 24
                and len(initial_business["reviews"]) == 8
            )
            assert (
                case["reference"]["prior_human_steps"]
                == case["reference"]["prior_audit"]
                == 96
            )
            assert before["business"]["reviews"][:8] == initial_business["reviews"]
    files = {
        str(p.relative_to(tmp_path / "old")): p.read_bytes()
        for p in (tmp_path / "old").rglob("*")
        if p.is_file()
    }
    reset = reset_case(tmp_path / "old", tmp_path / "new")
    assert reset["instance_id"] != info["instance_id"] and reset["generation"] == 2
    assert reset["source"]["business"] == initial_business
    assert (
        reset["source"]["records"]["operations"]
        == reset["source"]["records"]["decisions"]
        == []
    )
    assert {
        str(p.relative_to(tmp_path / "old")): p.read_bytes()
        for p in (tmp_path / "old").rglob("*")
        if p.is_file()
    } == files
    assert inspect_case(tmp_path / "old")["source_sha256"] == before["sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "control_rehash",
        "fixture",
        "seed_strip",
        "seed_rehash",
        "header_strip",
        "copied",
        "symlink",
        "overlap",
        "locked",
    ],
)
def test_private_controls_cannot_grant_imported_or_rehashed_authority_or_destroy_original(
    tmp_path, fault
):
    info = initialize(tmp_path / "old", "starter-01")
    root = tmp_path / "old"
    if fault == "control_rehash":
        path = root / "control.json"
        value = parse_json(path.read_bytes())
        value["reference"]["case_id"] = "starter-02"
        path.write_text(
            canonical(sealed({k: v for k, v in value.items() if k != "content_sha256"}))
        )
    elif fault == "fixture":
        (root / "fixture.json").write_bytes(b"{}")
    elif fault in ("seed_strip", "seed_rehash", "header_strip"):
        db = sqlite3.connect(info["database"])
        if fault == "seed_strip":
            db.execute("DELETE FROM seed_state")
        elif fault == "seed_rehash":
            value = json.loads(
                db.execute("SELECT document FROM seed_state").fetchone()[0]
            )
            value["original"]["tables"]["operations"] = [
                {
                    "id": "forged",
                    "document_base64": "e30=",
                    "document_sha256": sha256(b"{}"),
                }
            ]
            from contract_review.strict import digest

            value["original_sha256"] = digest(value["original"])
            value = sealed({k: v for k, v in value.items() if k != "content_sha256"})
            db.execute("UPDATE seed_state SET document=?", (canonical(value),))
        else:
            value = json.loads(
                db.execute("SELECT document FROM metadata").fetchone()[0]
            )
            value.update(creation="fixture", population=None)
            db.execute("UPDATE metadata SET document=?", (canonical(value),))
        db.commit()
        db.close()
    elif fault == "copied":
        shutil.copytree(root, tmp_path / "copied")
        root = tmp_path / "copied"
    elif fault == "symlink":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias"
    elif fault == "overlap":
        with pytest.raises(ValueError):
            reset_case(root, root / "nested")
        assert not (root / "nested").exists()
        return
    else:
        (root / ".case-control.lock").write_text("other-owner")
    before = {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with pytest.raises((ValueError, OSError)):
        inspect_case(root)
    assert {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


def test_package_asset_hardlink_is_allowed_but_mutable_import_requires_unshared_original(
    tmp_path, monkeypatch
):
    import os
    from contract_review import assets

    p = tmp_path / "data"
    p.mkdir()
    for name in assets.MANIFEST:
        os.link(assets.DATA_ROOT / name, p / name)
    monkeypatch.setattr(assets, "DATA_ROOT", p)
    assert assets.read_asset("starter.json")
    from contract_review.strict import read_original

    with pytest.raises(ValueError):
        read_original(p / "starter.json")


def test_default_private_cli_installed_starter_and_case_commands_outside_checkout(
    tmp_path,
):
    import subprocess, sys

    def call(module, *args):
        out = subprocess.run(
            [sys.executable, "-m", module, *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert out.returncode == 0, out.stderr + out.stdout
        return json.loads(out.stdout)

    initialized = call(
        "contract_review.managed_cli", "init", "--db", str(tmp_path / "default.db")
    )
    assert initialized["business"]["documents"][0]["id"] == "D-701"
    listing = call("contract_review.cases", "list")
    assert len(listing["cases"]) == 12
    first = call(
        "contract_review.cases",
        "init",
        "--case",
        "starter-01",
        "--root",
        str(tmp_path / "first"),
    )
    final = call(
        "contract_review.cases",
        "reset",
        "--root",
        str(tmp_path / "first"),
        "--new-root",
        str(tmp_path / "second"),
    )
    assert first["instance_id"] != final["instance_id"] and final["generation"] == 2
