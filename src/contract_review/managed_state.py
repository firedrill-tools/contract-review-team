"""Exact SQLite source images and replay of scoped contract observations/decisions."""

from __future__ import annotations

import base64
import re
from copy import deepcopy
from uuid import UUID

from .domain import (
    HUMAN_ROLE,
    ROLES,
    advance,
    document,
    next_stage,
    selected,
    transition,
)
from urllib.parse import urlsplit
from .source import PROVENANCE, parse
from .strict import canonical, digest, integer, keys, parse_json, sha256, text

APPLICATION_ID = 0x46444352
SOURCE_VERSION = 1
MAX_DATABASE_BYTES = 64 * 1024 * 1024
SCHEMA = {
    "metadata": "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "business": "CREATE TABLE business (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "operations": "CREATE TABLE operations (id TEXT PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "observations": "CREATE TABLE observations (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "decisions": "CREATE TABLE decisions (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "seed_state": "CREATE TABLE seed_state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
}
LIMITS = {
    "metadata": 1,
    "business": 1,
    "operations": 128,
    "observations": 1024,
    "decisions": 512,
    "seed_state": 1,
}


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).version != 4:
        raise ValueError("original UUID4 contract identity required")
    return value


def hex_digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("original SHA256 required")
    return value


def schema(db):
    if (
        db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or db.execute("PRAGMA user_version").fetchone()[0] != SOURCE_VERSION
        or db.execute("PRAGMA page_size").fetchone()[0] != 4096
        or db.execute("PRAGMA encoding").fetchone()[0] != "UTF-8"
        or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
    ):
        raise ValueError("original contract database header differs")
    if db.execute("PRAGMA page_count").fetchone()[0] * 4096 > MAX_DATABASE_BYTES:
        raise ValueError("contract database exceeds64MiB")
    actual = [
        tuple(row)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    ]
    expected = sorted(
        [("table", name, name, sql) for name, sql in SCHEMA.items()]
        + [("index", "sqlite_autoindex_operations_1", "operations", None)]
    )
    if actual != expected or [
        tuple(r) for r in db.execute("PRAGMA quick_check(1)")
    ] != [("ok",)]:
        raise ValueError("exact original contract SQL schema/integrity required")
    return actual


def rows(db, name):
    count = db.execute("SELECT count(*) FROM " + name).fetchone()[0]
    if count > LIMITS[name]:
        raise ValueError("bounded complete contract SQL rows required")
    original = []
    values = []
    for identity, raw in db.execute(
        "SELECT id,CAST(document AS BLOB) FROM " + name + " ORDER BY id"
    ):
        if type(raw) is not bytes:
            raise ValueError("original contract SQL text bytes required")
        value = parse_json(raw)
        if raw.decode("utf-8", errors="strict").encode("utf-8", errors="strict") != raw:
            raise ValueError("original contract SQL UTF8 differs")
        original.append(
            {
                "id": identity,
                "document_base64": base64.b64encode(raw).decode(),
                "document_sha256": sha256(raw),
            }
        )
        values.append((identity, value))
    return original, values


def header(value, physical, instance_id=None):
    keys(
        value,
        {
            "schemaVersion",
            "kind",
            "instance_id",
            "physical_identity",
            "fixture_base64",
            "fixture_sha256",
            "provenance",
            "creation",
            "population",
        },
    )
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != SOURCE_VERSION
        or value["kind"] != "managed_synthetic_contract_source"
        or value["physical_identity"] != physical
        or value["provenance"] != PROVENANCE
        or value["creation"] != "fixture"
        or value["population"] is not None
    ):
        raise ValueError("original contract source header identity/provenance differs")
    uuid(value["instance_id"])
    if instance_id is not None and value["instance_id"] != instance_id:
        raise ValueError("original contract source UUID differs")
    if type(value["fixture_base64"]) is not str:
        raise ValueError("literal contract initial fixture required")
    raw = base64.b64decode(value["fixture_base64"], validate=True)
    if (
        base64.b64encode(raw).decode() != value["fixture_base64"]
        or sha256(raw) != value["fixture_sha256"]
    ):
        raise ValueError("original contract fixture hash/encoding differs")
    return parse(raw)["business"]


def context(source, frontier, business):
    value = {
        "source_sha256": digest(source),
        "frontier": frontier,
        "business_sha256": digest(business),
    }
    return value, digest(value)


def caller_intent(business, document_id, request, context_sha256):
    text(request, 1, 8192)
    if not request.strip():
        raise ValueError("nonblank original contract review request required")
    return {
        "document_id": document_id,
        "document": deepcopy(selected(business, document_id)),
        "request": request,
        "context_sha256": hex_digest(context_sha256),
    }


def model_targets(value):
    keys(value, set(ROLES))
    for role, target in value.items():
        keys(target, {"provider", "model", "base_url", "credential_sha256"})
        if target["provider"] != ("anthropic" if role in ROLES[:2] else "openai"):
            raise ValueError("exact configured native role provider required")
        text(target["model"], 1, 200)
        hex_digest(target["credential_sha256"])
        text(target["base_url"], 1, 500)
        origin = urlsplit(target["base_url"])
        if (
            origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.hostname is None
            or origin.path not in ("", "/v1")
            or origin.scheme not in ("https", "http")
            or origin.scheme == "http"
            and (origin.hostname != "127.0.0.1" or origin.port is None)
        ):
            raise ValueError(
                "original native role HTTPS or owned loopback base required"
            )
    return value


def check_operation(value):
    keys(
        value,
        {
            "id",
            "token_sha256",
            "task_sha256",
            "intent",
            "review_id",
            "execution",
            "models",
            "initial_frontier",
            "allow_writes",
            "status",
            "closed_frontier",
            "closed_observations",
        },
    )
    uuid(value["id"])
    hex_digest(value["token_sha256"])
    hex_digest(value["task_sha256"])
    from .strict import identifier

    identifier(value["review_id"])
    integer(value["initial_frontier"])
    keys(value["intent"], {"document_id", "document", "request", "context_sha256"})
    if (
        value["execution"] not in ("native", "human")
        or type(value["allow_writes"]) is not bool
        or value["status"] not in ("active", "closed")
    ):
        raise ValueError("exact original review execution/write/status required")
    if value["execution"] == "native":
        model_targets(value["models"])
    elif value["models"] is not None:
        raise ValueError("human control seam cannot claim native model configuration")
    if value["status"] == "active":
        if (
            value["closed_frontier"] is not None
            or value["closed_observations"] is not None
        ):
            raise ValueError("active contract operation has closure frontier")
    else:
        integer(value["closed_frontier"])
        integer(value["closed_observations"])
        if value["closed_frontier"] < value["initial_frontier"]:
            raise ValueError("review closure precedes original caller frontier")


def public_read(business, operation, view):
    return {
        "document": deepcopy(selected(business, operation["intent"]["document_id"])),
        "review_id": operation["review_id"],
        "review": deepcopy(view),
        "stage": next_stage(view),
    }


def evaluate(business, operation, view, action, observation):
    keys(action, {"kind", "args"})
    if action["kind"] != "stage":
        raise ValueError(
            "owned review operation can only accept its next role/control stage"
        )
    args = keys(
        action["args"], {"review_id", "document", "stage", "payload", "provenance"}
    )
    document(args["document"])
    read = observation["value"]
    if (
        args["review_id"] != operation["review_id"]
        or args["document"] != operation["intent"]["document"]
        or read != public_read(business, operation, view)
        or args["stage"] != read["stage"]
        or observation["stage"] != read["stage"]
    ):
        raise ValueError(
            "genuine own exact current document/review/next-stage observation required"
        )
    if read["document"] != operation["intent"]["document"]:
        return business, {"status": "document_changed"}, "unchanged", view
    if args["stage"] in ROLES:
        if operation["execution"] == "human":
            if args["provenance"] != HUMAN_ROLE:
                raise ValueError(
                    "human-only control scope cannot claim native execution"
                )
        else:
            target = operation["models"][args["stage"]]
            proposed = args["provenance"]
            if (
                type(proposed) is not dict
                or proposed.get("kind") != "owned_native_role"
                or proposed.get("provider") != target["provider"]
                or proposed.get("model") != target["model"]
            ):
                raise ValueError(
                    "original configured native model/role provenance required"
                )
    after_view = advance(
        view,
        operation["review_id"],
        args["document"],
        args["stage"],
        args["payload"],
        args["provenance"],
        allow_native=operation["execution"] == "native",
    )
    if not operation["allow_writes"]:
        return (
            business,
            {
                "status": "write_permission_required",
                "stage": args["stage"],
                "preview_sha256": digest(after_view),
                "workflow_complete": False,
            },
            "denied",
            after_view,
        )
    actual = next(
        (row for row in business["reviews"] if row["id"] == operation["review_id"]),
        None,
    )
    if actual != view:
        raise ValueError("this operation's original actual review lineage differs")
    after, result = transition(
        business, action, allow_native=operation["execution"] == "native"
    )
    if (
        next(
            (row for row in after["reviews"] if row["id"] == operation["review_id"]),
            None,
        )
        != after_view
    ):
        raise ValueError("atomic review stage differs from owned control lineage")
    return after, result, "applied" if after != business else "unchanged", after_view


def snapshot(db, physical, instance_id=None):
    original_schema = schema(db)
    raw, table = {}, {}
    for name in SCHEMA:
        raw[name], table[name] = rows(db, name)
    if (
        [r[0] for r in table["metadata"]] != [1]
        or [r[0] for r in table["business"]] != [1]
        or table["seed_state"]
    ):
        raise ValueError("original contract singleton/fixture seed differs")
    source = table["metadata"][0][1]
    current = header(source, physical, instance_id)
    operations = {}
    for identity, operation in table["operations"]:
        check_operation(operation)
        if identity != operation["id"]:
            raise ValueError("exact original review SQL operation identity required")
        operations[identity] = operation
    review_ids = [operation["review_id"] for operation in operations.values()]
    if (
        len(review_ids) != len(set(review_ids))
        or sum(operation["status"] == "active" for operation in operations.values()) > 1
    ):
        raise ValueError("unique owned review targets and one active scope required")
    observations = {}
    for expected, (identity, value) in enumerate(table["observations"], 1):
        keys(
            value,
            {
                "id",
                "operation_id",
                "frontier",
                "context_sha256",
                "stage",
                "value",
                "proof_sha256",
            },
        )
        if (
            type(identity) is not int
            or identity != expected
            or type(value["id"]) is not int
            or value["id"] != identity
            or value["operation_id"] not in operations
        ):
            raise ValueError("exact original review observation SQL/owner required")
        integer(value["frontier"])
        hex_digest(value["context_sha256"])
        hex_digest(value["proof_sha256"])
        observations[identity] = value
    frontiers = [deepcopy(current)]
    view_frontiers = [{identity: None for identity in operations}]
    used, decisions = set(), []
    for ordinal, (identity, value) in enumerate(table["decisions"], 1):
        keys(
            value,
            {
                "id",
                "action",
                "operation_id",
                "read_id",
                "before_context_sha256",
                "after_context_sha256",
                "status",
                "result",
                "provenance",
            },
        )
        if (
            type(identity) is not int
            or identity != ordinal
            or type(value["id"]) is not int
            or value["id"] != ordinal
        ):
            raise ValueError("exact original contract decision frontier required")
        _, before = context(source, ordinal - 1, current)
        if value["before_context_sha256"] != before:
            raise ValueError("original contract decision before context differs")
        views = deepcopy(view_frontiers[-1])
        if value["operation_id"] is None:
            if (
                value["read_id"] is not None
                or value["provenance"] != "explicit_synthetic_human_document"
                or value["action"].get("kind") != "replace_document"
            ):
                raise ValueError(
                    "separate human boundary can only replace explicit current documents"
                )
            after, result = transition(current, value["action"])
            status = "applied" if after != current else "unchanged"
        else:
            if (
                type(value["operation_id"]) is not str
                or type(value["read_id"]) is not int
            ):
                raise ValueError("exact owned review decision/read required")
            operation = operations.get(value["operation_id"])
            read = observations.get(value["read_id"])
            if (
                operation is None
                or read is None
                or value["read_id"] in used
                or read["operation_id"] != operation["id"]
                or read["frontier"] != ordinal - 1
                or read["context_sha256"] != before
                or operation["initial_frontier"] > ordinal - 1
                or value["provenance"] != "managed_caller"
                or operation["status"] == "closed"
                and ordinal > operation["closed_frontier"]
            ):
                raise ValueError(
                    "genuine own current unused contract stage read required"
                )
            used.add(value["read_id"])
            after, result, status, views[operation["id"]] = evaluate(
                current, operation, views[operation["id"]], value["action"], read
            )
        _, after_context = context(source, ordinal, after)
        if (
            value["status"] != status
            or value["result"] != result
            or value["after_context_sha256"] != after_context
        ):
            raise ValueError(
                "complete original contract decision/effect replay differs"
            )
        current = after
        frontiers.append(deepcopy(current))
        view_frontiers.append(views)
        decisions.append(value)
    for operation in operations.values():
        frontier = operation["initial_frontier"]
        if (
            frontier >= len(frontiers)
            or operation["status"] == "closed"
            and (
                operation["closed_frontier"] >= len(frontiers)
                or operation["closed_observations"] > len(observations)
            )
        ):
            raise ValueError("original contract caller/closure frontier absent")
        _, ctx = context(source, frontier, frontiers[frontier])
        intent = operation["intent"]
        if intent != caller_intent(
            frontiers[frontier], intent["document_id"], intent["request"], ctx
        ) or any(
            row["id"] == operation["review_id"]
            for row in frontiers[frontier]["reviews"]
        ):
            raise ValueError(
                "complete original caller document intent or fresh owned target differs"
            )
    for read in observations.values():
        frontier = read["frontier"]
        operation = operations[read["operation_id"]]
        if (
            frontier >= len(frontiers)
            or frontier < operation["initial_frontier"]
            or operation["status"] == "closed"
            and (
                frontier > operation["closed_frontier"]
                or read["id"] > operation["closed_observations"]
            )
        ):
            raise ValueError("original contract observation frontier absent")
        _, ctx = context(source, frontier, frontiers[frontier])
        expected = public_read(
            frontiers[frontier], operation, view_frontiers[frontier][operation["id"]]
        )
        if (
            read["context_sha256"] != ctx
            or read["value"] != expected
            or read["stage"] != expected["stage"]
        ):
            raise ValueError("original contract observed document/stage/view differs")
    if table["business"][0][1] != current:
        raise ValueError(
            "current contract business differs from complete original replay"
        )
    ctx, ctx_sha = context(source, len(decisions), current)
    original = {"schema": original_schema, "tables": raw}
    return {
        "source": deepcopy(source),
        "business": deepcopy(current),
        "records": {
            "operations": list(operations.values()),
            "observations": list(observations.values()),
            "decisions": decisions,
        },
        "views": view_frontiers[-1],
        "context": ctx,
        "context_sha256": ctx_sha,
        "sha256": digest(original),
        "original": original,
    }
