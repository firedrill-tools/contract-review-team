# Contract review team with LangGraph, Anthropic and OpenAI

An actual async LangGraph runs extraction, two independent risk reviews, deterministic reconciliation, summary and packet publication over a local synthetic contract workspace. Anthropic performs extraction and one review; OpenAI independently reviews and summarizes. Each role uses the actual async provider SDK over a per-run owned HTTPX2 client. Whole original structured responses must be complete before SDK parsing or state changes.

Simulate this LangGraph AI agent with [Firedrill](https://firedrill.run/), a simulation platform for AI agents. Start with the [Firedrill setup and simulation guide](firedrill/README.md); your coding agent can operate it while this application's runner stays in your repository or CI.

This example repository is publicly viewable. A project license has not been selected.

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

Tests exercise actual compiled LangGraph and actual provider SDKs against owned synthetic HTTP responses, plus actual source files/SQLite/CLI/signals. No paid provider, model quality, legal correctness, actual billing or authenticated Firedrill persistence is qualified. The original six controlled-role compiled graph tests and original Markdown fixture remain useful human compatibility seams in `workflow.py`; that module's legacy `run_review` is not the normal owned CLI. Legacy LangChain dependencies remain for those seams. The installed Anthropic LangChain wrapper uses cached clients without a public supplied-client hook, so the normal graph uses the actual provider SDKs directly to own per-run lifecycle.

Live models/keys/budget, real contracts and legal review, authenticated Firedrill persistence verification, and license decisions remain external gates. This repository is publicly viewable; no project license has been selected. See [PLAN.md](PLAN.md) for implementation evidence and [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog) for the portfolio.

## Twelve installed cases and distributions

```sh
uv run contract-review-cases list
uv run contract-review-cases init --case busy-01 --root /tmp/contracts-busy
uv run contract-review-cases inspect --root /tmp/contracts-busy
uv run contract-review-cases reset --root /tmp/contracts-busy --new-root /tmp/contracts-busy-reset
uv run python scripts/generate_populations.py --check
uv build
uv run --group dev python scripts/check_distribution.py
```

Case setup/inspection returns private controls and reference outcomes. Use only the selected document/request/independent write flag for a normal review. Each of four profiles has three cases; busy cases include24contracts,8superseded human packets and96replayed human steps. See [scenario details](scenarios/README.md) and [qualification boundaries](COMPATIBILITY.md). Default `contract-review-state init --db NEW` uses the exact pinned installed starter; explicit Markdown/JSON imports still require original unshared files. The wheel also retains the original Markdown bytes as a pinned read-only asset.

Distribution qualification creates fresh locked wheel production, sdist production, complete full and complete production environments outside checkout; production has no pytest. All12cases run through actual native SDK HTTP and private CLI preserving resets, followed by normal CLI complete/unfinished/SIGINT/SIGTERM/SIGHUP/default-seed/missing-key checks. The complete suite and deterministic source manifest are also verified. CI uploads its complete source artifact for independent byte-for-byte comparison; the catalog records the exact qualified revision and evidence.

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks preserve actual private-case/native-key behavior and separate caller intent. Authenticated persisted runs and live quality/cost remain separate parked gates.
