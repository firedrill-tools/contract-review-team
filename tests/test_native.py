"""Actual async LangGraph/provider SDKs over owned synthetic HTTP, never paid models."""

import asyncio
import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from contract_review.domain import ROLES
from contract_review.owned_runtime import run_owned_review
from contract_review.strict import parse_json
from test_managed import seed
from test_policy import extracted, assessed, summarized


@contextmanager
def provider(mutate=None, before=None):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            role = body["model"].removeprefix("owned-")
            public = json.loads(body["messages"][-1]["content"])
            captured.append(
                {
                    "role": role,
                    "body": body,
                    "public": public,
                    "headers": dict(self.headers),
                    "path": self.path,
                }
            )
            if before:
                before(role, captured)
            doc = {"units": public["source"]["body_paragraph_units"]}
            if role == "extraction":
                payload = extracted(doc)
            elif role in ("risk_anthropic", "risk_openai"):
                payload = assessed(doc)
                payload["assessments"][0][
                    "rationale"
                ] = "Synthetic independently proposed reviewer rationale."
                if role == "risk_openai":
                    payload["assessments"][0]["severity"] = "high"
            else:
                payload = summarized(doc, public["reconciliation"])
            if role in ROLES[:2]:
                response = {
                    "id": "msg-" + role,
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-" + role,
                            "name": body["tools"][0]["name"],
                            "input": payload,
                        }
                    ],
                    "stop_reason": "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            else:
                response = {
                    "id": "chat-" + role,
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(payload),
                                "refusal": None,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            status = 200
            raw = None
            if mutate:
                returned = mutate(role, response, captured)
                if isinstance(returned, bytes):
                    raw = returned
                elif isinstance(returned, int):
                    status = returned
            raw = json.dumps(response).encode() if raw is None else raw
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def options(tmp_path, base):
    source = seed(tmp_path)
    path = source.path
    source.close()
    return {
        "database": path,
        "document_id": "D-701",
        "request": "Prepare this source-bound fictional review.",
        "allow_writes": True,
        "anthropic_key": "owned-anthropic-key",
        "openai_key": "owned-openai-key",
        "models": {r: "owned-" + r for r in ROLES},
        "anthropic_base": base,
        "openai_base": base,
        "output_root": tmp_path / "receipts",
    }


def receipt(result):
    return parse_json(__import__("pathlib").Path(result["receipt_path"]).read_bytes())


def test_actual_compiled_graph_four_original_complete_sdk_roles_and_independent_inputs(
    tmp_path,
):
    with provider() as (base, captured):
        result = asyncio.run(run_owned_review(**options(tmp_path, base)))
    evidence = receipt(result)
    assert [c["role"] for c in captured] == list(ROLES)
    assert all(c["sdk_verified"] and c["response_closed"] for c in evidence["calls"])
    assert captured[1]["public"] == captured[2]["public"]
    assert "independent_reviews" not in captured[1]["public"]
    packet = result["packet"]
    assert (
        packet["workflow_complete"]
        and packet["human_review_required"]
        and packet["legal_approval"] is None
    )
    assert packet["reconciliation"]["rating_differences"]
    assert result["business"]["report_saved"] and result["cleanup_confirmed"]
    assert len(evidence["acknowledged_effects"]) == 6
    assert (
        evidence["cleanup"]["provider_closed"]
        and evidence["cleanup"]["source_close_confirmed"]
    )
    assert result["cost_usd"] is None
    assert "owned-anthropic-key" not in json.dumps(
        evidence
    ) and "owned-openai-key" not in json.dumps(evidence)


def test_actual_readonly_graph_advances_preview_but_literal_business_stays_unchanged(
    tmp_path,
):
    with provider() as (base, captured):
        opts = options(tmp_path, base)
        from contract_review.managed import ManagedSource

        with ManagedSource(opts["database"]) as source:
            original = source.database.execute(
                "SELECT document FROM business"
            ).fetchone()[0]
        opts["allow_writes"] = False
        result = asyncio.run(run_owned_review(**opts))
    with ManagedSource(opts["database"]) as source:
        assert (
            source.database.execute("SELECT document FROM business").fetchone()[0]
            == original
        )
    assert len(captured) == 4 and result["packet"]["workflow_complete"]
    assert (
        not result["business"]["report_saved"]
        and result["business"]["preview_packet"] == result["packet"]
    )
    assert receipt(result)["acknowledged_effects"] == []


@pytest.mark.parametrize(
    "fault",
    [
        "missing_stop",
        "null_stop",
        "max_tokens",
        "wrong_schema",
        "wrong_payload_type",
        "duplicate",
        "nonfinite",
        "multiblock",
        "status",
        "oversize",
    ],
)
def test_original_anthropic_failure_precedes_sdk_normalization_and_any_effect(
    tmp_path, fault
):
    def mutate(role, response, captured):
        if role != "extraction":
            return
        if fault == "missing_stop":
            del response["stop_reason"]
        elif fault == "null_stop":
            response["stop_reason"] = None
        elif fault == "max_tokens":
            response["stop_reason"] = "max_tokens"
        elif fault == "wrong_schema":
            response["content"][0]["name"] = "OtherSchema"
        elif fault == "wrong_payload_type":
            response["content"][0]["input"]["units"][0]["evidence"]["start"] = "1"
        elif fault == "duplicate":
            return (
                json.dumps(response)
                .replace('"stop_reason":', '"stop_reason":"tool_use","stop_reason":')
                .encode()
            )
        elif fault == "nonfinite":
            return (
                json.dumps(response)
                .replace('"input_tokens": 10', '"input_tokens": NaN')
                .encode()
            )
        elif fault == "multiblock":
            response["content"].append({"type": "text", "text": "unaccepted"})
        elif fault == "status":
            return 429
        elif fault == "oversize":
            return b" " * (1024 * 1024 + 1)

    with provider(mutate) as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert len(captured) == 1 and ev["acknowledged_effects"] == []
    assert ev["cleanup_confirmed"] and ev["observed_business"]["review"] is None


@pytest.mark.parametrize("role", ["risk_openai", "summary"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing_finish",
        "null_finish",
        "length",
        "refusal",
        "duplicate_inner",
        "coerce",
        "multi_choice",
        "tool",
    ],
)
def test_original_openai_failure_keeps_prior_actual_roles_and_never_publishes(
    tmp_path, role, fault
):
    def mutate(current, response, captured):
        if current != role:
            return
        choice = response["choices"][0]
        if fault == "missing_finish":
            del choice["finish_reason"]
        elif fault == "null_finish":
            choice["finish_reason"] = None
        elif fault == "length":
            choice["finish_reason"] = "length"
        elif fault == "refusal":
            choice["message"]["refusal"] = "declined"
        elif fault == "duplicate_inner":
            choice["message"]["content"] = '{"overall":"medium","overall":"low"}'
        elif fault == "coerce":
            choice["message"]["content"] = "[]"
        elif fault == "multi_choice":
            response["choices"].append(deepcopy(choice))
        elif fault == "tool":
            choice["message"]["tool_calls"] = [{"id": "unapproved"}]

    with provider(mutate) as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert len(captured) == (3 if role == "risk_openai" else 4)
    assert len(ev["acknowledged_effects"]) == (2 if role == "risk_openai" else 4)
    assert not ev["observed_business"]["report_saved"] and ev["cleanup_confirmed"]


def test_incomplete_original_extraction_publishes_explicit_incomplete_packet_without_more_models(
    tmp_path,
):
    def mutate(role, response, captured):
        if role == "extraction":
            response["content"][0]["input"]["units"].pop()

    with provider(mutate) as (base, captured):
        result = asyncio.run(run_owned_review(**options(tmp_path, base)))
    assert len(captured) == 1 and not result["packet"]["workflow_complete"]
    assert (
        result["business"]["report_saved"] and result["packet"]["human_review_required"]
    )


def test_actual_document_revision_during_second_role_refuses_stale_observation_and_keeps_extraction(
    tmp_path,
):
    from contract_review.managed import ManagedSource
    from test_managed import MARKDOWN

    opts = None

    def before(role, captured):
        if role == "risk_anthropic":
            with ManagedSource(opts["database"]) as other:
                other.replace_document(
                    "D-701", MARKDOWN.read_bytes().replace(b"30 days", b"31 days")
                )

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_review(**opts))
    ev = exc.value.evidence
    assert len(captured) == 2 and len(ev["acknowledged_effects"]) == 1
    assert (
        not ev["observed_business"]["document_current"]
        and not ev["observed_business"]["report_saved"]
    )
    assert (
        len(ev["observed_business"]["review"]["observations"]) == 1
        and ev["cleanup_confirmed"]
    )


def test_summary_cannot_drop_actual_notice_and_rating_warning_references(tmp_path):
    def mutate(role, response, captured):
        if role == "summary":
            payload = json.loads(response["choices"][0]["message"]["content"])
            payload["notice_candidate_ids"] = payload["rating_difference_ids"] = []
            response["choices"][0]["message"]["content"] = json.dumps(payload)

    with provider(mutate) as (base, captured):
        with pytest.raises(ValueError, match="retain every actual") as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert len(captured) == 4 and len(ev["acknowledged_effects"]) == 4
    assert ev["observed_business"]["review"]["reconciliation"]["rating_differences"]
    assert not ev["observed_business"]["report_saved"] and ev["cleanup_confirmed"]


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 0, "output_tokens": 0},
        {"input_tokens": True, "output_tokens": -1},
    ],
)
def test_original_usage_unknown_zero_invalid_stays_distinct_from_sdk_and_billing(
    tmp_path, usage
):
    def mutate(role, response, captured):
        if role == "extraction":
            if usage is None:
                response.pop("usage")
            else:
                response["usage"] = usage
            response["unrecognized_provider_field"] = {"literal": "retained"}

    with provider(mutate) as (base, captured):
        result = asyncio.run(run_owned_review(**options(tmp_path, base)))
    ev = receipt(result)
    call = ev["calls"][0]
    assert call["response"]["unrecognized_provider_field"] == {"literal": "retained"}
    expected = (
        "unknown"
        if not usage
        else ("invalid" if type(usage["input_tokens"]) is bool else "observed")
    )
    assert call["reported_usage"]["input_tokens"]["status"] == expected
    assert call["reported_usage"]["cost_usd"] is None and result["cost_usd"] is None


def test_all_models_and_keys_validate_before_source_or_provider_allocation(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with provider() as (base, captured):
        opts = options(tmp_path, base)
        opts["openai_key"] = None
        with pytest.raises(ValueError, match="OPENAI_API_KEY") as exc:
            asyncio.run(run_owned_review(**opts))
    assert captured == [] and exc.value.evidence["allocation"] == {
        "source": False,
        "scope": False,
        "provider": False,
    }
    assert exc.value.evidence["cleanup_confirmed"]


def test_original_models_and_intent_frozen_before_actual_http_and_ambient_routing_ignored(
    tmp_path, monkeypatch
):
    for name, value in {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
        "OPENAI_BASE_URL": "http://127.0.0.1:1",
        "ANTHROPIC_AUTH_TOKEN": "unowned-token",
        "OPENAI_ORG_ID": "unowned-org",
        "OPENAI_PROJECT_ID": "unowned-project",
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": "unowned-trace-key",
        "LANGSMITH_ENDPOINT": "http://127.0.0.1:1",
    }.items():
        monkeypatch.setenv(name, value)
    opts = None

    def before(role, captured):
        if role == "extraction":
            opts["models"]["summary"] = "changed-after-call"
            opts["request"] = "changed-after-call"
            opts["allow_writes"] = False

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        original_request = opts["request"]
        result = asyncio.run(run_owned_review(**opts))
    assert result["business"]["report_saved"] and all(
        c["public"]["request"] == original_request for c in captured
    )
    assert captured[-1]["body"]["model"] == "owned-summary"
    assert all(
        not any(
            k.lower().startswith(("traceparent", "baggage", "x-langsmith"))
            for k in c["headers"]
        )
        for c in captured
    )
    assert "unowned-" not in json.dumps(receipt(result))


def test_unsupported_provider_rejected_after_prepared_receipt_before_uncopyable_payload(
    tmp_path,
):
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise AssertionError("should not copy unsupported provider")

    with pytest.raises(ValueError, match="unsupported") as exc:
        asyncio.run(
            run_owned_review(output_root=tmp_path / "receipts", provider=Uncopyable())
        )
    assert (
        exc.value.receipt_path and exc.value.evidence["allocation"]["source"] is False
    )


@pytest.mark.parametrize("kind", ["caller", "parent", "deadline", "direct"])
def test_real_pending_sdk_cancel_revokes_before_drain_and_preserves_first_role(
    tmp_path, kind
):
    entered, release = threading.Event(), threading.Event()

    def before(role, captured):
        if role == "risk_anthropic":
            entered.set()
            release.wait(2)

    async def run(opts):
        stop, parent = asyncio.Event(), asyncio.Event()
        opts.update(stop_event=stop, parent_event=parent)
        if kind == "deadline":
            opts["timeout_ms"] = 500
        task = asyncio.create_task(run_owned_review(**opts))
        while not entered.is_set() and not task.done():
            await asyncio.sleep(0.005)
        assert entered.is_set()
        if kind == "caller":
            stop.set()
        elif kind == "parent":
            parent.set()
        elif kind == "direct":
            task.cancel()
        try:
            with pytest.raises(Exception) as exc:
                await task
            return exc.value.evidence
        finally:
            release.set()

    with provider(before=before) as (base, captured):
        ev = asyncio.run(run(options(tmp_path, base)))
    assert len(ev["acknowledged_effects"]) == 1 and ev["cleanup_confirmed"]
    requested = [r for r in ev["lifecycle"] if r["kind"] == "flow_cancel_requested"]
    assert requested and all(r["scope_closed"] for r in requested)
    assert not ev["observed_business"]["report_saved"]


def test_partial_real_second_sdk_constructor_closes_both_owned_pools(
    tmp_path, monkeypatch
):
    import openai

    actual, allocated = openai.AsyncOpenAI, []

    def fail(**kwargs):
        allocated.append(actual(**kwargs))
        raise RuntimeError("second SDK constructor failed after actual allocation")

    monkeypatch.setattr(openai, "AsyncOpenAI", fail)
    with provider() as (base, captured):
        with pytest.raises(RuntimeError, match="constructor") as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    assert (
        allocated[0].is_closed()
        and captured == []
        and exc.value.evidence["cleanup_confirmed"]
    )


@pytest.mark.parametrize("resource", ["provider", "source", "recording"])
def test_late_real_cleanup_or_recording_failed_ack_keeps_published_business(
    tmp_path, monkeypatch, resource
):
    if resource == "provider":
        from contract_review.native import Pool

        actual = Pool.close

        async def close(pool):
            await actual(pool)
            if pool.provider == "anthropic":
                raise RuntimeError("actual provider closed but acknowledgment failed")

        monkeypatch.setattr(Pool, "close", close)
    elif resource == "source":
        from contract_review.managed import ManagedSource

        actual = ManagedSource.close

        def close(source):
            result = actual(source)
            if source.commit_guard is not None:
                raise RuntimeError("actual source closed but acknowledgment failed")
            return result

        monkeypatch.setattr(ManagedSource, "close", close)
    else:
        from contract_review.evidence import Receipt

        actual = Receipt.save

        def save(value):
            if value.data["observed_business"].get("report_saved"):
                import shutil

                if value.path.parent.exists():
                    shutil.rmtree(value.path.parent)
            return actual(value)

        monkeypatch.setattr(Receipt, "save", save)
    with provider() as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert (
        len(captured) == 4
        and ev["observed_business"]["report_saved"]
        and ev["observed_business"]["workflow_complete"]
    )
    assert len(ev["acknowledged_effects"]) == 6
    if resource == "recording":
        assert ev["recording_status"] == "unknown" and ev["cleanup_confirmed"]
    else:
        assert not ev["cleanup_confirmed"]


def cli_command(opts):
    import sys

    return [
        sys.executable,
        "-m",
        "contract_review.cli",
        "--db",
        str(opts["database"]),
        "--document",
        opts["document_id"],
        "--request",
        opts["request"],
        "--allow-writes",
        "--extract-model",
        opts["models"]["extraction"],
        "--risk-anthropic-model",
        opts["models"]["risk_anthropic"],
        "--risk-openai-model",
        opts["models"]["risk_openai"],
        "--summary-model",
        opts["models"]["summary"],
        "--anthropic-base",
        opts["anthropic_base"],
        "--openai-base",
        opts["openai_base"],
        "--output",
        str(opts["output_root"]),
    ]


def cli_env():
    import os

    result = dict(os.environ)
    result.update(
        ANTHROPIC_API_KEY="owned-anthropic-key", OPENAI_API_KEY="owned-openai-key"
    )
    return result


@pytest.mark.parametrize("incomplete", [False, True])
def test_normal_installed_cli_outside_checkout_uses_actual_sdk_and_closes_before_output(
    tmp_path, incomplete
):
    import subprocess

    def mutate(role, response, captured):
        if incomplete and role == "summary":
            response["choices"][0]["finish_reason"] = None

    with provider(mutate) as (base, captured):
        opts = options(tmp_path, base)
        output = subprocess.run(
            cli_command(opts),
            cwd=tmp_path,
            env=cli_env(),
            text=True,
            capture_output=True,
            timeout=15,
        )
    value = json.loads(output.stdout)
    assert len(captured) == 4 and output.returncode == int(incomplete)
    assert value["cleanup_confirmed"]
    if incomplete:
        assert (
            not value["business"]["report_saved"]
            and len(value["acknowledged_effects"]) == 4
        )
    else:
        assert (
            value["business"]["report_saved"] and value["packet"]["workflow_complete"]
        )


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_normal_actual_cli_all_os_signals_after_saved_extraction(tmp_path, signal_name):
    import signal
    import subprocess

    entered, release = threading.Event(), threading.Event()

    def before(role, captured):
        if role == "risk_anthropic":
            entered.set()
            release.wait(5)

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        child = subprocess.Popen(
            cli_command(opts),
            cwd=tmp_path,
            env=cli_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert entered.wait(8)
            child.send_signal(getattr(signal, signal_name))
            output, error = child.communicate(timeout=8)
        finally:
            release.set()
            if child.poll() is None:
                child.kill()
                child.wait()
    result = json.loads(output)
    assert child.returncode == 1 and result["observed_signal"] == signal_name
    assert result["cleanup_confirmed"] and len(result["acknowledged_effects"]) == 1
    assert len(captured) == 2 and not result["business"]["report_saved"]


def test_normal_cli_missing_key_fails_with_receipt_before_opening_source(tmp_path):
    import subprocess

    env = cli_env()
    env.pop("OPENAI_API_KEY")
    with provider() as (base, captured):
        opts = options(tmp_path, base)
        output = subprocess.run(
            cli_command(opts),
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
    value = json.loads(output.stdout)
    assert (
        output.returncode == 1
        and captured == []
        and value["business"]["status"] == "unopened"
    )
    assert value["receipt_path"] and value["cleanup_confirmed"]


@pytest.mark.parametrize("fault", ["null_stop", "partial_json"])
def test_real_second_anthropic_role_requires_complete_original_response_after_extraction(
    tmp_path, fault
):
    def mutate(role, response, captured):
        if role == "risk_anthropic":
            if fault == "null_stop":
                response["stop_reason"] = None
            else:
                return b'{"id":'

    with provider(mutate) as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_review(**options(tmp_path, base)))
    assert len(captured) == 2 and len(exc.value.evidence["acknowledged_effects"]) == 1
    assert (
        exc.value.evidence["cleanup_confirmed"]
        and not exc.value.evidence["observed_business"]["report_saved"]
    )


@pytest.mark.parametrize("fault", ["business_corrupt", "writer_lock"])
def test_actual_late_source_corruption_or_writer_lock_stays_unknown_without_repair(
    tmp_path, fault
):
    import sqlite3

    blocker = None
    opts = None

    def before(role, captured):
        nonlocal blocker
        if role == "risk_anthropic":
            blocker = sqlite3.connect(
                opts["database"], isolation_level=None, check_same_thread=False
            )
            if fault == "business_corrupt":
                blocker.execute("UPDATE business SET document='{}'")
            else:
                blocker.execute("BEGIN IMMEDIATE")

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        try:
            with pytest.raises(Exception) as exc:
                asyncio.run(run_owned_review(**opts))
        finally:
            if blocker is not None:
                if blocker.in_transaction:
                    blocker.execute("ROLLBACK")
                blocker.close()
    ev = exc.value.evidence
    assert len(captured) == 2 and len(ev["acknowledged_effects"]) == 1
    if fault == "business_corrupt":
        assert (
            ev["observed_business"]["status"] == "unknown" and ev["cleanup_confirmed"]
        )
        assert len(ev["observed_business"]["last_known"]["review"]["observations"]) == 1
    else:
        assert (
            ev["observed_business"]["status"] == "valid" and not ev["cleanup_confirmed"]
        )
        assert not ev["cleanup"]["scope_closed"]
