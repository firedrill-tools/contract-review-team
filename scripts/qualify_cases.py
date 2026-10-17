"""All installed private cases, actual graph/SDK HTTP and byte-preserving resets."""

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import importlib.util

spec = importlib.util.spec_from_file_location(
    "contract_owned_http", Path(__file__).with_name("owned_http.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
provider = module.provider
from contract_review.domain import ROLES
from contract_review.managed import ManagedSource
from contract_review.owned_runtime import run_owned_review
from contract_review.populations import definition
from contract_review.strict import sha256


def cli(*args):
    out = subprocess.run(
        [sys.executable, "-I", "-m", "contract_review.cases", *map(str, args)],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        timeout=120,
    )
    assert out.returncode == 0, out.stderr + out.stdout[-6000:]
    return json.loads(out.stdout)


def files(root):
    return {
        str(p.relative_to(root)): (p.stat().st_mode & 0o777, sha256(p.read_bytes()))
        for p in root.rglob("*")
        if p.is_file()
    }


def qualify(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    listing = cli("list")
    assert len(listing["cases"]) == 12
    rows = []
    for item in listing["cases"]:
        case = definition(item["id"])
        old = root / case["id"]
        info = cli("init", "--case", case["id"], "--root", old)
        assert cli("inspect", "--root", old)["source_sha256"] == info["source_sha256"]
        changed = False

        def before(role, captured):
            nonlocal changed
            during = case["during"]
            if during is not None and not changed and during["stage"] == role:
                with ManagedSource(info["database"]) as source:
                    source.replace_document(
                        during["document_id"],
                        base64.b64decode(during["markdown_base64"]),
                    )
                changed = True

        def mutate(role, response, captured):
            fault = case["native_fault"]
            if fault is None or fault["role"] != role:
                return
            if fault["kind"] == "missing_unit":
                response["content"][0]["input"][
                    "units" if role == "extraction" else "assessments"
                ].pop()
            elif fault["kind"] == "null_finish":
                response["choices"][0]["finish_reason"] = None
            elif fault["kind"] == "drop_warnings":
                value = json.loads(response["choices"][0]["message"]["content"])
                value["notice_candidate_ids"] = value["rating_difference_ids"] = []
                response["choices"][0]["message"]["content"] = json.dumps(value)

        statuses = []
        with provider(mutate, before) as (base, requests):
            for caller in case["runs"]:
                opts = {
                    "database": info["database"],
                    **caller,
                    "anthropic_key": "owned-anthropic-key",
                    "openai_key": "owned-openai-key",
                    "models": {r: "owned-" + r for r in ROLES},
                    "anthropic_base": base,
                    "openai_base": base,
                    "output_root": old / "receipts",
                }
                try:
                    result = asyncio.run(run_owned_review(**opts))
                    assert not case["reference"]["technical_failure"]
                    ev = json.loads(Path(result["receipt_path"]).read_bytes())
                    assert (
                        result["packet"]["workflow_complete"]
                        == case["reference"]["packet_complete"]
                    )
                    assert (
                        result["business"]["report_saved"]
                        == case["reference"]["report_saved"]
                    )
                    statuses.append("completed")
                except Exception as error:
                    assert case["reference"]["technical_failure"], repr(error)
                    ev = error.evidence
                    assert json.loads(
                        Path(error.receipt_path).read_bytes()
                    ) == json.loads(json.dumps(ev))
                    statuses.append("failed")
                assert ev["cleanup_confirmed"] and ev["recording_status"] == "durable"
                assert all(c.get("sdk_verified") for c in ev["calls"] if not c["error"])
            assert len(requests) == case["reference"]["expected_calls"], (
                case["id"],
                len(requests),
            )
            wire = json.dumps(requests)
            for private in (
                case["reference"]["private_canary"],
                case["id"],
                info["instance_id"],
                info["seed_sha256"],
                "definition_sha256",
                "dataset_sha256",
                "fixture_base64",
                "token_sha256",
                "private_reference",
                "prior_human_steps",
            ):
                assert private not in wire, private
        final = cli("inspect", "--root", old)
        assert all(
            o["status"] == "closed" for o in final["source"]["records"]["operations"]
        )
        assert (
            final["source"]["business"]["reviews"][: info["metrics"]["reviews"]]
            == info["source"]["business"]["reviews"]
        )
        assert (
            final["source"]["business"]["audit"][: info["metrics"]["audit"]]
            == info["source"]["business"]["audit"]
        )
        old_files = files(old)
        reset = cli(
            "reset", "--root", old, "--new-root", root / (case["id"] + "-reset")
        )
        assert files(old) == old_files
        assert (
            reset["generation"] == 2
            and reset["instance_id"] != info["instance_id"]
            and reset["seed_sha256"] != info["seed_sha256"]
        )
        assert reset["source"]["business"] == info["source"]["business"]
        assert all(
            reset["source"]["records"][k] == []
            for k in ("operations", "observations", "decisions")
        )
        rows.append(
            {
                "id": case["id"],
                "statuses": statuses,
                "model_requests": len(requests),
                "prior_metrics": info["metrics"],
                "preserving_reset": True,
            }
        )
    return {
        "count": len(rows),
        "cases": rows,
        "model_requests": sum(c["model_requests"] for c in rows),
        "execution": "actual installed async LangGraph and provider SDKs over owned HTTP; fictional human history",
        "real_provider_or_external_effect": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.root:
        result = qualify(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="contract-installed-cases-") as root:
            result = qualify(root)
    text = json.dumps(result, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(text)
    print(text)
