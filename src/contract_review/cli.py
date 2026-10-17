"""Normal owned SDK CLI with explicit caller action flags and cooperative OS signals."""

import argparse
import asyncio
import json
import signal

from .owned_runtime import run_owned_review
from .strict import parse_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--document", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--extract-model")
    parser.add_argument("--risk-anthropic-model")
    parser.add_argument("--risk-openai-model")
    parser.add_argument("--summary-model")
    parser.add_argument("--anthropic-base")
    parser.add_argument("--openai-base")
    parser.add_argument("--intent-json")
    parser.add_argument("--output", default="artifacts/contract-review")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    args = parser.parse_args()
    observed_signal = None

    async def run():
        nonlocal observed_signal
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def cancel(name):
            nonlocal observed_signal
            observed_signal = name
            stop.set()

        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        for value in signals:
            loop.add_signal_handler(value, cancel, value.name)
        try:
            return await run_owned_review(
                database=args.db,
                document_id=args.document,
                request=args.request,
                allow_writes=args.allow_writes,
                models=(
                    {
                        "extraction": args.extract_model,
                        "risk_anthropic": args.risk_anthropic_model,
                        "risk_openai": args.risk_openai_model,
                        "summary": args.summary_model,
                    }
                    if any(
                        (
                            args.extract_model,
                            args.risk_anthropic_model,
                            args.risk_openai_model,
                            args.summary_model,
                        )
                    )
                    else None
                ),
                anthropic_base=args.anthropic_base,
                openai_base=args.openai_base,
                output_root=args.output,
                timeout_ms=args.timeout_ms,
                intent=(
                    parse_json(args.intent_json.encode("utf-8", errors="strict"))
                    if args.intent_json
                    else None
                ),
                stop_event=stop,
            )
        finally:
            for value in signals:
                loop.remove_signal_handler(value)

    try:
        print(
            json.dumps(
                asyncio.run(run()), ensure_ascii=False, allow_nan=False, indent=2
            )
        )
    except Exception as error:
        evidence = getattr(error, "evidence", {})
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "receipt_path": getattr(error, "receipt_path", None),
                    "observed_signal": observed_signal,
                    "business": evidence.get("observed_business"),
                    "acknowledged_effects": evidence.get("acknowledged_effects"),
                    "cleanup_confirmed": evidence.get("cleanup_confirmed"),
                },
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
