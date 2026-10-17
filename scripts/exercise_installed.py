"""Installed production actual CLI/SDK/HTTP, prior stages, signals and preserving reset."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import contract_review
from contract_review.domain import ROLES

BASE = Path(sys.argv[1])
BASE.mkdir()
assert (
    Path(contract_review.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
)
if "--production-only" in sys.argv:
    assert importlib.util.find_spec("pytest") is None
spec = importlib.util.spec_from_file_location(
    "contract_owned_http", Path(__file__).with_name("owned_http.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BIN = Path(sys.executable).parent
ENV = {
    k: os.environ[k]
    for k in ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "SystemRoot", "WINDIR")
    if k in os.environ
}


def execute(command, expected=0, env=ENV, timeout=120):
    result = subprocess.run(
        list(map(str, command)),
        cwd=BASE,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == expected, (result.stdout[-6000:], result.stderr[-5000:])
    return json.loads(result.stdout)


probe = execute(
    [
        sys.executable,
        "-I",
        Path(__file__).with_name("qualify_cases.py"),
        "--root",
        BASE / "populations",
    ],
    timeout=900,
)
assert probe["count"] == 12 and probe["model_requests"] == 46
launcher = BASE / "contract-review"
launcher.symlink_to(BIN / "contract-review")
proof = []
for mode in ("completed", "unfinished-final", "SIGINT", "SIGTERM", "SIGHUP"):
    case = execute(
        [
            BIN / "contract-review-cases",
            "init",
            "--case",
            "starter-01",
            "--root",
            BASE / mode,
        ]
    )
    entered, release = threading.Event(), threading.Event()

    def before(role, captured):
        if mode.startswith("SIG") and role == "risk_anthropic":
            entered.set()
            release.wait(35)

    def mutate(role, response, captured):
        if mode == "unfinished-final" and role == "summary":
            response["choices"][0]["finish_reason"] = None

    with module.provider(mutate, before) as (base, requests):
        command = [
            launcher,
            "--db",
            case["database"],
            "--document",
            "D-701",
            "--request",
            "Prepare this exact source-bound fictional contract packet.",
            "--allow-writes",
            "--extract-model",
            "owned-extraction",
            "--risk-anthropic-model",
            "owned-risk_anthropic",
            "--risk-openai-model",
            "owned-risk_openai",
            "--summary-model",
            "owned-summary",
            "--anthropic-base",
            base,
            "--openai-base",
            base,
            "--output",
            BASE / mode / "receipts",
        ]
        child = subprocess.Popen(
            list(map(str, command)),
            cwd=BASE,
            env={
                **ENV,
                "ANTHROPIC_API_KEY": "owned-anthropic-key",
                "OPENAI_API_KEY": "owned-openai-key",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if mode.startswith("SIG"):
                assert entered.wait(20)
                child.send_signal(getattr(signal, mode))
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == (0 if mode == "completed" else 1), (
                stdout[-5000:],
                stderr[-5000:],
            )
            result = json.loads(stdout)
            path = Path(result["receipt_path"])
            ev = json.loads(path.read_bytes())
            expected_effects = (
                6 if mode == "completed" else (4 if mode == "unfinished-final" else 1)
            )
            expected_calls = 2 if mode.startswith("SIG") else 4
            assert (
                ev["cleanup_confirmed"]
                and len(ev["acknowledged_effects"]) == expected_effects
                and len(requests) == expected_calls
            )
            assert ev["observed_business"]["report_saved"] == (mode == "completed")
            assert len(ev["observed_business"]["review"]["observations"]) == (
                4 if mode == "completed" else (3 if mode == "unfinished-final" else 1)
            )
            assert "owned-anthropic-key" not in json.dumps(
                ev
            ) and "owned-openai-key" not in json.dumps(ev)
            if mode.startswith("SIG"):
                assert result["observed_signal"] == mode
            final = execute(
                [BIN / "contract-review-cases", "inspect", "--root", case["root"]]
            )
            assert all(
                o["status"] == "closed"
                for o in final["source"]["records"]["operations"]
            )
            source_bytes, evidence_bytes = (
                Path(case["database"]).read_bytes(),
                path.read_bytes(),
            )
            reset = execute(
                [
                    BIN / "contract-review-cases",
                    "reset",
                    "--root",
                    case["root"],
                    "--new-root",
                    BASE / (mode + "-reset"),
                ]
            )
            assert (
                reset["instance_id"] != case["instance_id"] and reset["generation"] == 2
            )
            assert reset["source"]["business"] == case["source"]["business"]
            assert (
                source_bytes == Path(case["database"]).read_bytes()
                and evidence_bytes == path.read_bytes()
            )
            proof.append(
                {
                    "mode": mode,
                    "requests": expected_calls,
                    "acknowledged_effects": expected_effects,
                    "prior_stages_retained": True,
                    "cleanup_confirmed": True,
                    "preserving_reset": True,
                }
            )
        finally:
            release.set()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

default = execute([BIN / "contract-review-state", "init", "--db", BASE / "default.db"])
assert (
    len(default["business"]["documents"]) == 1
    and default["source"]["creation"] == "fixture"
)
missing = execute(
    [
        launcher,
        "--db",
        BASE / "absent.db",
        "--document",
        "D-701",
        "--request",
        "Inspect only.",
        "--output",
        BASE / "missing-key",
    ],
    expected=1,
)
assert not (BASE / "absent.db").exists()
ev = json.loads(Path(missing["receipt_path"]).read_bytes())
assert ev["allocation"] == {"source": False, "scope": False, "provider": False}
print(
    json.dumps(
        {
            "cases": 12,
            "case_model_requests": probe["model_requests"],
            "normal_sdk_cli": proof,
            "default_seed": True,
            "missing_key_before_source": True,
            "production_only": "--production-only" in sys.argv,
            "real_provider_or_external_effect": False,
        }
    )
)
