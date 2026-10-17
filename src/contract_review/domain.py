"""Pure versioned documents and ordered review records; current authority is separate."""

import base64
import re
from copy import deepcopy

from . import policy
from .document import parse_markdown
from .strict import digest, identifier, integer, keys, next_revision, sha256, text

ROLES = ("extraction", "risk_anthropic", "risk_openai", "summary")
HUMAN_ROLE = {
    "kind": "explicit_synthetic_human_role",
    "model": None,
    "authentication": None,
    "legal_approval": None,
}
DOCUMENT_FIELDS = {"id", "title", "revision", "markdown_base64", "markdown_sha256"}
REVIEW_FIELDS = {
    "id",
    "document",
    "observations",
    "reconciliation",
    "packet",
    "revision",
}


def hex_digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("original contract SHA256 required")
    return value


def make_document(identity, raw, revision=1):
    identifier(identity)
    integer(revision, 1)
    parsed = parse_markdown(raw)
    return {
        "id": identity,
        "title": parsed["title"],
        "revision": revision,
        "markdown_base64": base64.b64encode(raw).decode(),
        "markdown_sha256": sha256(raw),
    }


def document(value):
    keys(value, DOCUMENT_FIELDS)
    identifier(value["id"])
    integer(value["revision"], 1)
    if type(value["markdown_base64"]) is not str:
        raise ValueError("literal original Markdown bytes required")
    raw = base64.b64decode(value["markdown_base64"], validate=True)
    if (
        base64.b64encode(raw).decode() != value["markdown_base64"]
        or sha256(raw) != value["markdown_sha256"]
    ):
        raise ValueError("original Markdown encoding/hash differs")
    parsed = parse_markdown(raw)
    if parsed["title"] != value["title"]:
        raise ValueError("current title differs from literal original Markdown")
    return parsed


def provenance(value, role, allow_native=True):
    if value == HUMAN_ROLE:
        return value
    keys(
        value,
        {
            "kind",
            "provider",
            "model",
            "request_sha256",
            "response_sha256",
            "legal_approval",
        },
    )
    if (
        not allow_native
        or value["kind"] != "owned_native_role"
        or value["provider"]
        != ("anthropic" if role in ("extraction", "risk_anthropic") else "openai")
        or value["legal_approval"] is not None
    ):
        raise ValueError(
            "original role provenance cannot import native/authentication/approval authority"
        )
    text(value["model"], 1, 200)
    hex_digest(value["request_sha256"])
    hex_digest(value["response_sha256"])
    return value


def selected(value, identity):
    identifier(identity)
    row = next((d for d in value["documents"] if d["id"] == identity), None)
    if row is None:
        raise ValueError("unknown current contract document")
    return row


def next_stage(value):
    if value is None:
        return "extraction"
    if value["packet"] is not None:
        return None
    observations = value["observations"]
    if len(observations) == 1:
        return (
            "risk_anthropic" if observations[0]["validation"]["complete"] else "publish"
        )
    if len(observations) == 2:
        return "risk_openai"
    if len(observations) == 3:
        if value["reconciliation"] is None:
            return "reconcile"
        return (
            "summary" if value["reconciliation"]["comparison_complete"] else "publish"
        )
    return "publish"


def validate_review(value):
    keys(value, REVIEW_FIELDS)
    identifier(value["id"])
    parsed = document(value["document"])
    observations = value["observations"]
    if type(observations) is not list or not 1 <= len(observations) <= 4:
        raise ValueError("complete bounded ordered review observations required")
    if type(value["revision"]) is not int or value["revision"] != len(
        observations
    ) + int(value["reconciliation"] is not None) + int(value["packet"] is not None):
        raise ValueError("review revision differs from complete stage history")
    prior = []
    for ordinal, entry in enumerate(observations):
        keys(entry, {"role", "payload", "validation", "provenance"})
        if entry["role"] != ROLES[ordinal]:
            raise ValueError(
                "exact original extraction/reviewer/summary ordering required"
            )
        provenance(entry["provenance"], entry["role"])
        if ordinal == 0:
            validation = policy.extraction(parsed, entry["payload"])
        elif ordinal < 3:
            if not prior[0]["validation"]["complete"]:
                raise ValueError(
                    "risk review cannot follow incomplete source extraction"
                )
            validation = policy.review(parsed, entry["payload"])
        else:
            if (
                value["reconciliation"] is None
                or not value["reconciliation"]["comparison_complete"]
            ):
                raise ValueError(
                    "summary requires actual complete independent reconciliation"
                )
            validation = policy.summary(
                parsed, value["reconciliation"], entry["payload"]
            )
        if validation != entry["validation"]:
            raise ValueError("original role source validation differs from replay")
        prior.append(entry)
    if value["reconciliation"] is not None:
        if len(observations) < 3 or value["reconciliation"] != policy.reconcile(
            parsed,
            observations[0]["payload"],
            observations[1]["payload"],
            observations[2]["payload"],
        ):
            raise ValueError("original independent review reconciliation differs")
    if value["packet"] is not None:
        pending = {**value, "packet": None}
        if next_stage(pending) != "publish" or value["packet"] != computed_packet(
            pending
        ):
            raise ValueError("packet lacks its complete original accepted role lineage")
    return value


def computed_packet(value):
    roles = {item["role"]: item["payload"] for item in value["observations"]}
    return policy.packet(
        document(value["document"]),
        roles["extraction"],
        roles.get("risk_anthropic"),
        roles.get("risk_openai"),
        roles.get("summary"),
    )


def advance(
    before,
    review_id,
    original,
    stage,
    payload=None,
    role_provenance=None,
    *,
    allow_native=False,
):
    """One source-bound role/control transition, also used by private managed control views."""
    identifier(review_id)
    parsed = document(original)
    if before is not None:
        validate_review(before)
        if before["id"] != review_id or before["document"] != original:
            raise ValueError("exact original review/document snapshot required")
    if next_stage(before) != stage:
        raise ValueError("genuine next ordered review stage required")
    value = (
        deepcopy(before)
        if before is not None
        else {
            "id": review_id,
            "document": deepcopy(original),
            "observations": [],
            "reconciliation": None,
            "packet": None,
            "revision": 0,
        }
    )
    if stage in ROLES:
        provenance(role_provenance, stage, allow_native)
        if stage == "extraction":
            validation = policy.extraction(parsed, payload)
        elif stage in ("risk_anthropic", "risk_openai"):
            validation = policy.review(parsed, payload)
        else:
            validation = policy.summary(parsed, value["reconciliation"], payload)
        value["observations"].append(
            {
                "role": stage,
                "payload": deepcopy(payload),
                "validation": validation,
                "provenance": deepcopy(role_provenance),
            }
        )
    else:
        if payload is not None or role_provenance is not None:
            raise ValueError(
                "computed reconciliation/publication cannot accept model-supplied data"
            )
        if stage == "reconcile":
            values = value["observations"]
            value["reconciliation"] = policy.reconcile(
                parsed, values[0]["payload"], values[1]["payload"], values[2]["payload"]
            )
        elif stage == "publish":
            value["packet"] = computed_packet(value)
        else:
            raise ValueError("known computed review stage required")
    value["revision"] = next_revision(value["revision"]) if before is not None else 1
    validate_review(value)
    return value


def validate(value):
    keys(value, {"documents", "reviews", "audit"})
    if (
        type(value["documents"]) is not list
        or not 1 <= len(value["documents"]) <= 32
        or type(value["reviews"]) is not list
        or len(value["reviews"]) > 128
        or type(value["audit"]) is not list
        or len(value["audit"]) > 2048
    ):
        raise ValueError("complete bounded contract workspace tables required")
    document_ids = []
    for row in value["documents"]:
        document(row)
        document_ids.append(row["id"])
    review_ids = []
    for row in value["reviews"]:
        validate_review(row)
        if row["document"]["id"] not in document_ids:
            raise ValueError("historical review refers to an unknown document")
        review_ids.append(row["id"])
    if len(document_ids) != len(set(document_ids)) or len(review_ids) != len(
        set(review_ids)
    ):
        raise ValueError("unique complete contract/review IDs required")
    for entry in value["audit"]:
        keys(
            entry,
            {
                "event",
                "document_id",
                "review_id",
                "role",
                "before_sha256",
                "after_sha256",
                "revision",
            },
        )
        if entry["document_id"] not in document_ids or entry["event"] not in (
            "document_replaced",
            "role_accepted",
            "review_reconciled",
            "packet_published",
        ):
            raise ValueError("exact original contract audit linkage required")
        if entry["event"] == "document_replaced":
            if entry["review_id"] is not None or entry["role"] is not None:
                raise ValueError("document update cannot import review authority")
        elif entry["review_id"] not in review_ids or entry["role"] not in (
            ROLES if entry["event"] == "role_accepted" else (None,)
        ):
            raise ValueError("original review audit identity/role differs")
        hex_digest(entry["before_sha256"])
        hex_digest(entry["after_sha256"])
        integer(entry["revision"], 1)
    return value


def initial(value):
    explicit = type(value) is dict and "baselineVersion" in value
    keys(
        value,
        {"documents", "reviews", "audit"}
        | ({"baselineVersion"} if explicit else set()),
    )
    if explicit and (
        type(value["baselineVersion"]) is not int or value["baselineVersion"] != 1
    ):
        raise ValueError("explicit contract baselineVersion1 required")
    result = {key: deepcopy(value[key]) for key in ("documents", "reviews", "audit")}
    validate(result)
    if (
        result["reviews"]
        or result["audit"]
        or not explicit
        and any(row["revision"] != 1 for row in result["documents"])
    ):
        raise ValueError(
            "original fixture cannot import prior review/native/audit authority"
        )
    return result


def transition(value, action, *, allow_native=False):
    validate(value)
    keys(action, {"kind", "args"})
    kind, args = action["kind"], action["args"]
    if kind == "replace_document":
        keys(args, {"document_id", "source", "markdown_base64"})
        document(args["source"])
        if (
            args["source"]["id"] != args["document_id"]
            or type(args["markdown_base64"]) is not str
        ):
            raise ValueError("exact original replacement document required")
        raw = base64.b64decode(args["markdown_base64"], validate=True)
        if base64.b64encode(raw).decode() != args["markdown_base64"]:
            raise ValueError("literal replacement Markdown encoding required")
        parsed = parse_markdown(raw)
        current = deepcopy(value)
        row = selected(current, args["document_id"])
        if row != args["source"]:
            return current, {"status": "document_changed"}
        if row["markdown_base64"] == args["markdown_base64"]:
            return current, {"status": "unchanged"}
        before = digest(row)
        replacement = make_document(row["id"], raw, next_revision(row["revision"]))
        row.update(replacement)
        audit = {
            "event": "document_replaced",
            "document_id": row["id"],
            "review_id": None,
            "role": None,
            "before_sha256": before,
            "after_sha256": digest(row),
            "revision": row["revision"],
        }
        result = {
            "status": "document_replaced",
            "document_id": row["id"],
            "revision": row["revision"],
        }
    elif kind == "stage":
        keys(args, {"review_id", "document", "stage", "payload", "provenance"})
        document(args["document"])
        current = deepcopy(value)
        actual = selected(current, args["document"]["id"])
        if actual != args["document"]:
            return current, {"status": "document_changed"}
        prior = next(
            (r for r in current["reviews"] if r["id"] == args["review_id"]), None
        )
        if prior is not None and prior["packet"] is not None:
            return current, {"status": "already_published"}
        after = advance(
            prior,
            args["review_id"],
            args["document"],
            args["stage"],
            args["payload"],
            args["provenance"],
            allow_native=allow_native,
        )
        if prior is None:
            current["reviews"].append(after)
        else:
            current["reviews"][current["reviews"].index(prior)] = after
        stage = args["stage"]
        audit = {
            "event": (
                "role_accepted"
                if stage in ROLES
                else "review_reconciled" if stage == "reconcile" else "packet_published"
            ),
            "document_id": actual["id"],
            "review_id": after["id"],
            "role": stage if stage in ROLES else None,
            "before_sha256": digest(prior),
            "after_sha256": digest(after),
            "revision": after["revision"],
        }
        result = {
            "status": "packet_published" if stage == "publish" else "stage_accepted",
            "review_id": after["id"],
            "stage": stage,
            "review_revision": after["revision"],
            "workflow_complete": (
                after["packet"]["workflow_complete"]
                if after["packet"] is not None
                else False
            ),
        }
    else:
        raise ValueError("known exact contract workspace action required")
    current["audit"].append(audit)
    validate(current)
    return current, result
