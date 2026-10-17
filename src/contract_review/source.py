"""Complete literal human contract origin/history; never current SDK caller authority."""

import base64
from copy import deepcopy

from .domain import initial, transition, validate as validate_business
from .strict import digest, keys, parse_json, sha256

PROVENANCE = {
    "kind": "explicit_synthetic_human_contract_source",
    "prior_model": None,
    "authentication": None,
    "legal_approval": None,
}


def validate(value):
    if type(value) is dict and set(value) in (
        {"documents", "reviews", "audit"},
        {"documents", "reviews", "audit", "baselineVersion"},
    ):
        return {"business": initial(value), "origin": None, "history": []}
    keys(value, {"schemaVersion", "origin", "business", "history"})
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("human contract source schemaVersion1 required")
    origin = keys(value["origin"], {"fixture_base64", "fixture_sha256", "provenance"})
    if type(origin["fixture_base64"]) is not str:
        raise ValueError("literal original contract fixture required")
    raw = base64.b64decode(origin["fixture_base64"], validate=True)
    if (
        base64.b64encode(raw).decode() != origin["fixture_base64"]
        or sha256(raw) != origin["fixture_sha256"]
        or origin["provenance"] != PROVENANCE
    ):
        raise ValueError("original contract fixture hash/provenance differs")
    current = initial(parse_json(raw))
    if type(value["history"]) is not list or not 1 <= len(value["history"]) <= 2048:
        raise ValueError("complete bounded human contract history required")
    for ordinal, step in enumerate(value["history"], 1):
        keys(step, {"ordinal", "action", "before_sha256", "after_sha256", "result"})
        if (
            type(step["ordinal"]) is not int
            or step["ordinal"] != ordinal
            or step["before_sha256"] != digest(current)
        ):
            raise ValueError("original human contract history frontier differs")
        after, result = transition(current, step["action"])
        if (
            after == current
            or step["after_sha256"] != digest(after)
            or step["result"] != result
        ):
            raise ValueError("original human contract history effect/result differs")
        current = after
    validate_business(value["business"])
    if value["business"] != current:
        raise ValueError(
            "current contract state differs from complete human history replay"
        )
    return deepcopy(value)


def parse(raw):
    return validate(parse_json(raw))


def apply(raw, action):
    before = parse(raw)
    after, result = transition(before["business"], action)
    if after == before["business"]:
        return None, result
    origin = before["origin"] or {
        "fixture_base64": base64.b64encode(raw).decode(),
        "fixture_sha256": sha256(raw),
        "provenance": deepcopy(PROVENANCE),
    }
    value = {
        "schemaVersion": 1,
        "origin": origin,
        "business": after,
        "history": [
            *before["history"],
            {
                "ordinal": len(before["history"]) + 1,
                "action": deepcopy(action),
                "before_sha256": digest(before["business"]),
                "after_sha256": digest(after),
                "result": deepcopy(result),
            },
        ],
    }
    validate(value)
    return value, result
