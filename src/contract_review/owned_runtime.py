"""Owned normal compiled async LangGraph workflow and lifecycle; all effects are local synthetic state."""

import asyncio
import time
from copy import deepcopy
from datetime import datetime, timezone

from .domain import selected, document, ROLES
from .evidence import Receipt, error_info
from .managed import ManagedSource
from .native import Native, config
from .strict import canonical, parse_json, digest

TASK = "Prepare this exact current fictional contract review packet with independent role proposals and human legal review pending."


class Run:
    def __init__(self, receipt, timeout_ms):
        self.receipt = receipt
        self.deadline = time.monotonic() + timeout_ms / 1000
        self.source = self.scope = self.native = self.job = self.intent = None
        self.failure = None
        self.closing = self.scope_close_attempted = self.scope_closed = False
        self.flow_drained = True
        self.source_attempted = False
        self.stop_events = []
        self.active_role = "extraction"
        self.reads, self.agents = {}, {}
        self.allow_writes = False

    def fail(self, error):
        if self.failure is None:
            self.failure = error

    def save(self):
        try:
            self.receipt.save()
        except BaseException as error:
            self.fail(error)
            raise

    def basic(self):
        if self.failure is not None:
            raise self.failure
        if self.closing:
            raise RuntimeError("contract review operation is closing")
        if any(event.is_set() for event in self.stop_events):
            raise RuntimeError("contract review cancellation requested")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("contract review shared deadline exceeded")
        if self.receipt.error is not None:
            raise self.receipt.error

    def check(self):
        self.basic()
        if self.source is not None and self.scope is not None:
            self.source._operation(self.source.snapshot(), self.scope)

    def close_scope(self):
        if self.scope_close_attempted:
            return
        if self.scope is None:
            self.scope_closed = True
            return
        self.scope_close_attempted = True
        try:
            result = self.source.close_operation(self.scope)
            self.scope_closed = result["closed"]
            self.receipt.data["scope_close"] = result
            self.receipt.data["lifecycle"].append(
                {"kind": "scope_close_returned", "closed": self.scope_closed}
            )
        except BaseException as error:
            self.receipt.data["scope_close_error"] = error_info(error)
            self.fail(error)

    def cancel(self, error):
        if self.closing:
            self.fail(error)
            return
        self.closing = True
        self.fail(error)
        self.close_scope()
        if self.job is not None and not self.job.done():
            self.receipt.data["lifecycle"].append(
                {"kind": "flow_cancel_requested", "scope_closed": self.scope_closed}
            )
            self.job.cancel()

    def read(self, stage):
        self.check()
        read = self.source.observe(self.scope, stage)
        self.receipt.data["actions"].append(
            {
                "kind": "own_current_stage_read",
                "role": stage,
                "observation": deepcopy(read["observation"]),
                "source": self.receipt.source(self.source.snapshot()),
            }
        )
        self.save()
        return read

    def apply(self, read, payload=None, provenance=None):
        self.check()
        value = read["value"]
        action = {
            "kind": "stage",
            "args": {
                "review_id": value["review_id"],
                "document": deepcopy(value["document"]),
                "stage": value["stage"],
                "payload": deepcopy(payload),
                "provenance": deepcopy(provenance),
            },
        }
        returned = self.source.commit(
            self.scope, action, read["observation"]["id"], read["proof"]
        )
        self.receipt.data["acknowledged_decisions"].append(deepcopy(returned))
        if returned["decision"]["status"] == "applied":
            self.receipt.data["acknowledged_effects"].append(deepcopy(returned))
        snapshot = self.source.snapshot()
        self.receipt.data["observed_business"] = observed(
            snapshot, self.intent, self.scope
        )
        self.receipt.data["actions"].append(
            {
                "kind": "stage_returned",
                "role": value["stage"],
                "returned": deepcopy(returned),
                "source": self.receipt.source(snapshot),
            }
        )
        self.save()
        return snapshot["views"][self.scope.id]


def observed(source, intent, scope=None):
    current = selected(source["business"], intent["document_id"])
    view = source["views"].get(scope.id) if scope is not None else None
    saved = next(
        (
            r
            for r in source["business"]["reviews"]
            if view is not None and r["id"] == view["id"]
        ),
        None,
    )
    packet = None if view is None else deepcopy(view["packet"])
    return {
        "status": "valid",
        "as_observed_context_sha256": source["context_sha256"],
        "current_document": deepcopy(current),
        "original_document": deepcopy(intent["document"]),
        "document_current": current == intent["document"],
        "review": deepcopy(saved),
        "preview_packet": packet if saved is None else None,
        "report_saved": saved is not None and saved["packet"] is not None,
        "workflow_complete": bool(
            packet and packet["workflow_complete"] and current == intent["document"]
        ),
        "human_review_required": True,
        "legal_approval": None,
        "actual_external_effect": None,
    }


def reported_usage(calls):
    return {
        "source": "original provider JSON counters per actual HTTP call; no estimate or billing proof",
        "calls": [
            {
                "role": c["role"],
                "provider": c["provider"],
                "usage": deepcopy(c.get("reported_usage")),
            }
            for c in calls
        ],
        "cost_usd": None,
    }


async def run_owned_review(**options):
    allowed = {
        "database",
        "document_id",
        "request",
        "allow_writes",
        "intent",
        "anthropic_key",
        "openai_key",
        "models",
        "anthropic_base",
        "openai_base",
        "output_root",
        "timeout_ms",
        "stop_event",
        "parent_event",
    }
    signals = [options.get("stop_event"), options.get("parent_event")]
    receipt, owner, timer, watchers, result = None, None, None, [], None
    try:
        receipt = Receipt(
            options.get("output_root") or "artifacts/contract-review",
            {
                "execution": "actual_owned_async_LangGraph_Anthropic_OpenAI_SDK",
                "allocation": {"source": False, "scope": False, "provider": False},
                "observed_business": {
                    "status": "unopened",
                    "actual_external_effect": None,
                },
            },
        )
        if set(options) - allowed:
            raise ValueError("unsupported model/client/provider or execution option")
        if options.get("models") is not None and (
            type(options["models"]) is not dict
            or any(type(v) is not str for v in options["models"].values())
        ):
            raise ValueError(
                "explicit role model identifiers required; model objects unsupported"
            )
        frozen = {
            k: deepcopy(v)
            for k, v in options.items()
            if k not in ("stop_event", "parent_event")
        }
        timeout_ms = frozen.get("timeout_ms", 120000)
        if type(timeout_ms) is not int or not 10 <= timeout_ms <= 300000:
            raise ValueError("exact bounded timeout_ms10..300000 required")
        owner = Run(receipt, timeout_ms)
        if type(frozen.get("allow_writes", False)) is not bool:
            raise ValueError("exact independent allow_writes required")
        owner.allow_writes = frozen.get("allow_writes", False)
        for event in signals:
            if event is not None:
                if not isinstance(event, asyncio.Event):
                    raise ValueError("actual asyncio cancellation Event required")
                owner.stop_events.append(event)
        owner.basic()
        configuration = config(
            **{
                k: frozen.get(k)
                for k in (
                    "anthropic_key",
                    "openai_key",
                    "anthropic_base",
                    "openai_base",
                    "models",
                )
            }
        )
        owner.basic()
        loop = asyncio.get_running_loop()
        timer = loop.call_later(
            timeout_ms / 1000,
            lambda: owner.cancel(TimeoutError("contract shared deadline exceeded")),
        )
        for event in owner.stop_events:

            async def watch(signal=event):
                await signal.wait()
                owner.cancel(RuntimeError("contract cancellation requested"))

            watchers.append(asyncio.create_task(watch()))
        owner.source_attempted = True
        try:
            owner.source = ManagedSource(
                frozen.get("database"), commit_guard=owner.basic
            )
        except BaseException as error:
            owner.source = getattr(error, "managed_source", None)
            receipt.data["allocation"]["source"] = owner.source is not None and (
                owner.source.database is not None or owner.source.descriptor is not None
            )
            raise
        receipt.data["allocation"]["source"] = True
        initial = owner.source.snapshot()
        receipt.data["initial_source"] = receipt.source(initial)
        owner.intent = owner.source.intent(
            frozen.get("document_id"), frozen.get("request")
        )
        if frozen.get("intent") is not None and frozen["intent"] != owner.intent:
            raise ValueError("complete current caller document intent required")
        receipt.data["caller_intent"] = deepcopy(owner.intent)
        receipt.data["observed_business"] = observed(initial, owner.intent)
        owner.scope = owner.source.begin(
            owner.intent,
            TASK,
            allow_writes=owner.allow_writes,
            models=configuration.targets(),
        )
        receipt.data["allocation"]["scope"] = True
        try:
            owner.native = Native(owner, configuration)
        except BaseException as error:
            owner.native = getattr(error, "native_owner", None)
            receipt.data["allocation"]["provider"] = owner.native is not None and bool(
                owner.native.pools
            )
            raise
        receipt.data["allocation"]["provider"] = True
        from .owned_graph import run_graph

        owner.job = asyncio.create_task(run_graph(owner))
        owner.flow_drained = False
        result = await asyncio.shield(owner.job)
        owner.flow_drained = True
        owner.check()
        receipt.data["graph_final"] = deepcopy(result)
        owner.save()
    except BaseException as error:
        if receipt is None:
            receipt = getattr(error, "receipt", None)
        if owner is None and receipt is not None:
            owner = Run(receipt, 120000)
        if owner is None:
            raise
        owner.fail(error)
    finally:
        if owner is not None:
            owner.closing = True
            owner.close_scope()

            async def finish():
                owner.close_scope()
                if owner.job is not None:
                    if not owner.job.done():
                        receipt.data["lifecycle"].append(
                            {
                                "kind": "flow_cancel_requested",
                                "scope_closed": owner.scope_closed,
                            }
                        )
                        owner.job.cancel()
                    await asyncio.gather(owner.job, return_exceptions=True)
                    owner.flow_drained = True
                provider_confirmed = owner.native is None
                if owner.native is not None:
                    try:
                        await owner.native.close()
                        provider_confirmed = owner.native.closed
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["provider_close_error"] = error_info(error)
                source_confirmed, cleanup = owner.source is None, None
                if owner.source is not None:
                    try:
                        snapshot = owner.source.snapshot()
                        receipt.data["final_source"] = receipt.source(snapshot)
                        if owner.intent is not None:
                            receipt.data["observed_business"] = observed(
                                snapshot, owner.intent, owner.scope
                            )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["observed_business"] = {
                            "status": "unknown",
                            "last_known": receipt.data["observed_business"],
                            "error": error_info(error),
                            "actual_external_effect": None,
                        }
                    try:
                        cleanup = owner.source.close()
                        source_confirmed = (
                            cleanup["database_closed"]
                            or not cleanup["database_close_attempted"]
                        ) and (
                            cleanup["descriptor_closed"]
                            or not cleanup["descriptor_close_attempted"]
                        )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["source_close_error"] = error_info(error)
                        cleanup = {
                            "database_close_attempted": owner.source.database_close_attempted,
                            "database_closed": owner.source.database_closed,
                            "descriptor_close_attempted": owner.source.descriptor_close_attempted,
                            "descriptor_closed": owner.source.descriptor_closed,
                            "close_errors": deepcopy(owner.source.close_errors),
                        }
                for task in watchers:
                    task.cancel()
                if watchers:
                    await asyncio.gather(*watchers, return_exceptions=True)
                if timer is not None:
                    timer.cancel()
                if time.monotonic() >= owner.deadline:
                    owner.fail(
                        TimeoutError("contract review deadline exceeded during cleanup")
                    )
                receipt.data["cleanup"] = {
                    "scope_close_attempted": owner.scope_close_attempted,
                    "scope_closed": owner.scope_closed,
                    "flow_drained": owner.flow_drained,
                    "provider_close_attempted": (
                        owner.native.close_attempted if owner.native else False
                    ),
                    "provider_closed": owner.native.closed if owner.native else True,
                    "provider_close_confirmed": provider_confirmed,
                    "source_close_confirmed": source_confirmed,
                    "source": cleanup,
                }
                receipt.data["cleanup_confirmed"] = (
                    owner.scope_closed
                    and owner.flow_drained
                    and provider_confirmed
                    and source_confirmed
                )
                if not receipt.data["cleanup_confirmed"]:
                    owner.fail(RuntimeError("contract review cleanup unconfirmed"))
                receipt.data["source_attempted"] = owner.source_attempted
                receipt.data["finished_at"] = datetime.now(timezone.utc).isoformat()
                receipt.data["status"] = "failed" if owner.failure else "completed"
                receipt.data["error"] = (
                    error_info(owner.failure) if owner.failure else None
                )
                receipt.data["result"] = (
                    {
                        "status": "completed" if owner.allow_writes else "read_only",
                        "packet": receipt.data["graph_final"]["packet"],
                        "human_review_required": True,
                        "legal_approval": None,
                        "business": receipt.data["observed_business"],
                        "usage": reported_usage(receipt.data["calls"]),
                        "cost_usd": None,
                        "receipt_path": str(receipt.path),
                        "cleanup_confirmed": receipt.data["cleanup_confirmed"],
                        "actual_external_effect": None,
                    }
                    if owner.failure is None
                    else None
                )
                try:
                    owner.save()
                    if time.monotonic() >= owner.deadline:
                        raise TimeoutError(
                            "contract review deadline exceeded during final recording"
                        )
                except BaseException as error:
                    owner.fail(error)
                    receipt.data["status"] = "failed"
                    receipt.data["result"] = None
                    receipt.data["error"] = error_info(owner.failure)
                    if receipt.error is None:
                        try:
                            receipt.save()
                        except BaseException:
                            pass

            cleanup_task = asyncio.create_task(finish())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    owner.fail(error)
            cleanup_task.result()
    if owner.failure is not None:
        error = (
            RuntimeError("contract review caller cancelled")
            if isinstance(owner.failure, asyncio.CancelledError)
            else owner.failure
        )
        error.evidence = receipt.data
        error.receipt_path = str(receipt.path) if receipt.path else None
        if error is not owner.failure:
            raise error from owner.failure
        raise error
    return receipt.data["result"]
