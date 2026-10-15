# Contract review team

A LangGraph workflow runs separate extraction, Anthropic risk review, OpenAI risk review and summary roles over a synthetic numbered agreement. It checks that every clause has a sourced extraction, preserves a deterministic notice-period conflict, and reports reviewer disagreement instead of smoothing it away. Incomplete or unsupported extraction stops before the risk and summary stages.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --group dev
```

Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and enabled model IDs for `CONTRACT_EXTRACT_MODEL`, `CONTRACT_RISK_ANTHROPIC_MODEL`, `CONTRACT_RISK_OPENAI_MODEL` and `CONTRACT_SUMMARY_MODEL`, then run:

```sh
uv run contract-review --contract fixtures/meridian-services.md --output review.report.json
```

The fixture contains six clauses. C1 gives a 30-day renewal notice while C2 gives a 60-day termination notice; both are surfaced in the report. C3 through C6 contain breach, payment, service-level and data-deletion terms. The extractor must cite an exact span from each numbered clause. A missing clause or fabricated citation yields `status: incomplete` and `human_review_required: true`. Two risk reviewers can give different overall ratings; the report retains both, lists unsupported references, and marks the disagreement for human review. The summary is a model narrative alongside these structured checks, not a legal approval.

`uv run --group dev pytest` exercises the compiled graph with controlled role responses: complete extraction, missed clause, unsupported evidence, contradictory notice periods, reviewer disagreement and a consistent-clause case. These tests verify workflow and evidence gates; they do not prove live model extraction quality or legal correctness. Clause coverage alone cannot guarantee every obligation inside a clause was captured. Live provider and new Firedrill integration proofs remain pending. This private repository has no license pending portfolio publication decisions.

Explore related applications in the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog). Product and setup references: [Firedrill](https://firedrill.run/) and the [official developer documentation](https://docs.firedrill.run/). Use documentation matching the qualified release; this repository records its current integration limits above.
