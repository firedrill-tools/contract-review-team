"""Whole original complete structured provider responses before SDK normalization."""

from .schemas import SCHEMAS
from .strict import integer, parse_json, text

MAX_RESPONSE_BYTES = 1024 * 1024


def counter(value, name):
    if type(value) is not dict or name not in value:
        return {"status": "unknown", "value": None}
    try:
        integer(value[name])
    except ValueError:
        return {"status": "invalid", "value": None}
    return {"status": "observed", "value": value[name]}


def usage_of(body, provider):
    usage = body.get("usage") if type(body) is dict else None
    names = (
        (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        if provider == "anthropic"
        else ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    return {
        "source": "original "
        + provider
        + " JSON counters; no estimate or billing proof",
        **{name: counter(usage, name) for name in names},
        "cost_usd": None,
    }


def completed(raw, provider, role):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("original structured response exceeds1MiB")
    body = parse_json(raw)
    if type(body) is not dict or body.get("error") is not None:
        raise ValueError("original provider response object without an error required")
    text(body.get("id"), 1, 240)
    text(body.get("model"), 1, 240)
    schema = SCHEMAS[role]
    if provider == "anthropic":
        if (
            body.get("type") != "message"
            or body.get("role") != "assistant"
            or body.get("stop_reason") != "tool_use"
            or "stop_sequence" not in body
            or body["stop_sequence"] is not None
        ):
            raise ValueError(
                "explicit complete original Anthropic structured tool response required"
            )
        content = body.get("content")
        if (
            type(content) is not list
            or len(content) != 1
            or type(content[0]) is not dict
        ):
            raise ValueError("one complete original structured tool block required")
        block = content[0]
        if block.get("type") != "tool_use" or block.get("name") != schema.__name__:
            raise ValueError("exact original role schema tool required")
        text(block.get("id"), 1, 240)
        payload, tool_id, content_json = block.get("input"), block["id"], None
    else:
        if (
            body.get("object") != "chat.completion"
            or type(body.get("choices")) is not list
            or len(body["choices"]) != 1
        ):
            raise ValueError("one complete original OpenAI structured choice required")
        choice = body["choices"][0]
        if (
            type(choice) is not dict
            or type(choice.get("index")) is not int
            or choice["index"] != 0
            or choice.get("finish_reason") != "stop"
        ):
            raise ValueError("explicit complete original OpenAI stop required")
        message = choice.get("message")
        if (
            type(message) is not dict
            or message.get("role") != "assistant"
            or message.get("refusal") is not None
            or message.get("tool_calls") not in (None, [])
            or message.get("function_call") is not None
        ):
            raise ValueError(
                "original tool-free unrefused OpenAI assistant JSON required"
            )
        text(message.get("content"), 2, MAX_RESPONSE_BYTES)
        content_json = message["content"]
        payload, tool_id = (
            parse_json(content_json.encode("utf-8", errors="strict")),
            None,
        )
    # Strict parsing precedes the actual SDK and is compared again after it returns.
    parsed = schema.model_validate(payload, strict=True).model_dump(mode="json")
    if parsed != payload:
        raise ValueError(
            "original structured role data cannot be coerced or normalized"
        )
    return body, {
        "response_id": body["id"],
        "reported_model": body["model"],
        "schema": schema.__name__,
        "payload": payload,
        "tool_id": tool_id,
        "content_json": content_json,
        "completion": "tool_use" if provider == "anthropic" else "stop",
    }
