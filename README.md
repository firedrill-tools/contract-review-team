# Contract review team

An actual async LangGraph runs extraction, two independent risk reviews, deterministic reconciliation, summary and packet publication over a local synthetic contract workspace. Anthropic performs extraction and one review; OpenAI independently reviews and summarizes. Each role uses the actual async provider SDK over a per-run owned HTTPX2 client. Whole original structured responses must be complete before SDK parsing or state changes.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --group dev
uv run contract-review-state init --db contract.db --contract fixtures/meridian-services.md
```

Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and explicitly enabled model IDs in `CONTRACT_EXTRACT_MODEL`, `CONTRACT_RISK_ANTHROPIC_MODEL`, `CONTRACT_RISK_OPENAI_MODEL` and `CONTRACT_SUMMARY_MODEL`:

```sh
uv run contract-review --db contract.db --document D-701 \
  --request 'Prepare this exact synthetic contract review packet.' \
  --allow-writes --output artifacts/contract-review
```

Omit `--allow-writes` for a preview: all normal graph/model stages run, and private control decisions advance, while business documents, reviews and audit remain byte-for-byte unchanged. The output is private evidence and a source-bound packet. It always requires human legal review and has `legal_approval: null`. Current document changes invalidate a pending role; earlier accepted stages remain in their original historical review. A separate model-free `contract-review-state replace` revises a document with explicit current-source checks.

## What the packet proves

Original Markdown, numbered headings and exact Unicode codepoint spans are preserved. Formal coverage concerns complete body paragraphs. Exact literal spans do not prove semantic or obligation completeness. Renewal/termination notice differences are grounded review candidates, without deciding legal incompatibility. Both independent reviews, same-overall per-unit rating differences, different rationales and cross references remain visible. A summary must retain every actual structured warning and its source references. Model narrative is a proposal, never legal approval.

No automatic retry, fallback, ambient provider routing, shared client cache, tracing, imported runtime capability or model-provided computed reconciliation/publication is used. Explicit independent write authority, genuine current role reads and ordered stage lineage gate each atomic effect. Evidence distinguishes technical failure, known prior effects, current source validity, recording failure and failed cleanup acknowledgments. Caller/parent/deadline cancellation and SIGINT/SIGTERM/SIGHUP revoke scope before draining the actual graph and closing both provider pools and the SQLite owner.

## Verification and compatibility

```sh
uv run --locked --group dev pytest -q
```

Tests exercise actual compiled LangGraph and actual provider SDKs against owned synthetic HTTP responses, plus actual source files/SQLite/CLI/signals. No paid provider, model quality, legal correctness, actual billing or new Firedrill persisted integration is qualified. The original six controlled-role compiled graph tests and original Markdown fixture remain useful human compatibility seams in `workflow.py`; that module's legacy `run_review` is not the normal owned CLI. Legacy LangChain dependencies remain for those seams. The installed Anthropic LangChain wrapper uses cached clients without a public supplied-client hook, so the normal graph uses the actual provider SDKs directly to own per-run lifecycle.

Live models/keys/budget, real contracts and legal review, new Firedrill release qualification, publication and license decisions remain external gates. This repository remains private without a license pending that decision. See [PLAN.md](PLAN.md) for implementation evidence and [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog) for the portfolio.
