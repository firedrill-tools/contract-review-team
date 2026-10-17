"""Complete literal numbered Markdown with codepoint spans; no legal interpretation."""

import re

from .strict import integer, keys, sha256, text

MAX_MARKDOWN_BYTES = 64 * 1024


def parse_markdown(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MARKDOWN_BYTES:
        raise ValueError("bounded complete original contract Markdown required")
    source = raw.decode("utf-8", errors="strict")
    text(source, 1, MAX_MARKDOWN_BYTES)
    if "\r" in source:
        raise ValueError("canonical LF contract Markdown required")
    title = re.match(r"\A# ([^\n]+)\n", source)
    if title is None:
        raise ValueError("one original contract title required")
    text(title[1], 1, 200)
    headings = list(re.finditer(r"(?m)^## (C[1-9][0-9]*) ([^\n]+)\n", source))
    all_headings = list(re.finditer(r"(?m)^##[^\n]*(?:\n|\Z)", source))
    if (
        not 1 <= len(headings) <= 64
        or [h.start() for h in headings] != [h.start() for h in all_headings]
        or source[title.end() : headings[0].start()].strip()
        or re.search(r"(?m)^# ", source[title.end() :])
    ):
        raise ValueError(
            "complete exact numbered headings without omitted preamble required"
        )
    clauses, units = [], []
    for index, heading in enumerate(headings):
        if heading[1] != f"C{index+1}":
            raise ValueError("unique consecutive original clause IDs required")
        text(heading[2], 1, 200)
        raw_start = heading.end()
        raw_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(source)
        )
        content = source[raw_start:raw_end]
        start = raw_start + len(content) - len(content.lstrip())
        end = raw_end - (len(content) - len(content.rstrip()))
        body = source[start:end]
        text(body, 1, 8192)
        clause = {
            "id": heading[1],
            "title": heading[2],
            "start": start,
            "end": end,
            "text": body,
        }
        clauses.append(clause)
        boundaries = [
            (-1, 0),
            *[(m.start(), m.end()) for m in re.finditer(r"\n[ \t]*\n", body)],
            (len(body), len(body)),
        ]
        ordinal = 0
        for n in range(len(boundaries) - 1):
            left, right = boundaries[n][1], boundaries[n + 1][0]
            block = body[left:right]
            if not block.strip():
                continue
            ordinal += 1
            unit_start = start + left + len(block) - len(block.lstrip())
            unit_end = start + right - (len(block) - len(block.rstrip()))
            quote = source[unit_start:unit_end]
            text(quote, 1, 4096)
            units.append(
                {
                    "id": f"{heading[1]}-P{ordinal}",
                    "clause_id": heading[1],
                    "start": unit_start,
                    "end": unit_end,
                    "quote": quote,
                }
            )
    if not 1 <= len(units) <= 128:
        raise ValueError("complete bounded contract source units required")
    return {
        "sha256": sha256(raw),
        "bytes": len(raw),
        "title": title[1],
        "text": source,
        "clauses": clauses,
        "units": units,
    }


def unit(document, identity):
    if type(identity) is not str:
        raise ValueError("exact current source unit identifier required")
    value = next((item for item in document["units"] if item["id"] == identity), None)
    if value is None:
        raise ValueError("unknown current contract source unit")
    return value


def span(document, value, within=None):
    keys(value, {"start", "end", "quote"})
    integer(value["start"])
    integer(value["end"], 1)
    text(value["quote"], 1, 4096)
    if (
        not 0 <= value["start"] < value["end"] <= len(document["text"])
        or value["quote"] != document["text"][value["start"] : value["end"]]
    ):
        raise ValueError("exact original codepoint source span required")
    if (
        within is not None
        and not within["start"] <= value["start"] < value["end"] <= within["end"]
    ):
        raise ValueError("literal span must remain within its declared source unit")
    return value


def entire(unit):
    return {key: unit[key] for key in ("start", "end", "quote")}


def notice_candidates(document):
    periods = []
    pattern = re.compile(
        r"\b(renewal|termination) notice.{0,80}?\b([1-9][0-9]{0,4}) days\b", re.I | re.S
    )
    for item in document["units"]:
        for match in pattern.finditer(item["quote"]):
            start, end = item["start"] + match.start(), item["start"] + match.end()
            periods.append(
                {
                    "unit_id": item["id"],
                    "clause_id": item["clause_id"],
                    "notice_kind": match[1].lower(),
                    "days": int(match[2]),
                    "source": {
                        "start": start,
                        "end": end,
                        "quote": document["text"][start:end],
                    },
                }
            )
    return (
        [
            {
                "id": "NOTICE-001",
                "kind": "literal_notice_period_difference",
                "periods": periods,
                "interpretation": "bounded literal difference candidate; legal compatibility requires human review",
            }
        ]
        if len({p["days"] for p in periods}) > 1
        else []
    )
