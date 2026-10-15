from pathlib import Path

from contract_review.workflow import (Extraction, Finding, Obligation, RiskReview,
                                      Summary, build_graph, notice_conflicts,
                                      parse_contract, run_review)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "meridian-services.md"


def extract_all(state):
    return Extraction(obligations=[Obligation(clause_id=clause["id"], party="named party",
                                               action=f"Review obligation in {clause['id']}", deadline="as written",
                                               evidence=clause["text"][:40])
                                   for clause in state["clauses"]])


def review(severity):
    return lambda _state: RiskReview(overall=severity,
                                    findings=[Finding(clause_ids=["C1", "C2"], severity=severity,
                                                      rationale="Notice periods conflict in the source clauses.")],
                                    rationale="The notice terms require human reconciliation.")


def summary(state):
    if state["conflict"]:
        return Summary(narrative="C1 and C2 have conflicting notice periods; counsel should resolve them before approval.")
    return Summary(narrative="The notice periods agree across C1 and C2; review the remaining terms before approval.")


def test_clause_parser_and_dual_review_preserve_conflict_and_disagreement():
    clauses = parse_contract(FIXTURE)
    assert [item["id"] for item in clauses] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    assert notice_conflicts(clauses)[0]["periods"] == [{"clause_id": "C1", "days": 30}, {"clause_id": "C2", "days": 60}]
    report = run_review(FIXTURE, extract_all, review("high"), review("medium"), summary)
    assert report["status"] == "needs_human_review"
    assert report["human_review_required"] is True
    assert report["validation"]["complete"] is True
    assert len(report["obligations"]) == 6
    assert report["risk_reviews"]["anthropic"]["overall"] == "high"
    assert report["risk_reviews"]["openai"]["overall"] == "medium"
    assert report["disagreement"]["reviewer_disagreement"] is True
    assert "C1 and C2" in report["summary"]


def test_missed_clause_blocks_risk_and_summary_roles():
    def missing(state):
        all_obligations = extract_all(state).obligations
        return Extraction(obligations=all_obligations[:-1])
    unused = lambda _state: (_ for _ in ()).throw(AssertionError("later model must not run"))
    report = run_review(FIXTURE, missing, unused, unused, unused)
    assert report["status"] == "incomplete"
    assert report["validation"]["missing_clause_ids"] == ["C6"]
    assert report["human_review_required"] is True


def test_unsupported_quote_or_unknown_clause_blocks_review():
    def fabricated(state):
        obligations = extract_all(state).obligations
        obligations[0].evidence = "This clause does not exist in the source."
        obligations.append(Obligation(clause_id="C99", party="vendor", action="Invented obligation",
                                      deadline="never", evidence="A fabricated source excerpt"))
        return Extraction(obligations=obligations)
    unused = lambda _state: (_ for _ in ()).throw(AssertionError("later model must not run"))
    report = run_review(FIXTURE, fabricated, unused, unused, unused)
    assert report["status"] == "incomplete"
    assert set(report["validation"]["unsupported_evidence"]) == {"C1", "C99"}


def test_invalid_reviewer_reference_is_visible_in_report():
    def bad_review(_state):
        return RiskReview(overall="medium", findings=[Finding(clause_ids=["C99"], severity="medium",
                                                              rationale="Unsupported reference in reviewer output")],
                          rationale="This review needs verification.")
    report = run_review(FIXTURE, extract_all, bad_review, review("medium"), summary)
    assert report["status"] == "needs_human_review"
    assert report["disagreement"]["invalid_references"] == ["C99"]


def test_agreeing_reviewers_on_consistent_terms_can_complete(tmp_path):
    source = FIXTURE.read_text().replace("termination notice at least 60 days", "termination notice at least 30 days")
    file = tmp_path / "consistent.md"; file.write_text(source)
    report = run_review(file, extract_all, review("medium"), review("medium"), summary)
    assert report["status"] == "reviewed"
    assert report["conflicts"] == []
    assert report["disagreement"]["reviewer_disagreement"] is False


def test_compiled_langgraph_exposes_report_node():
    graph = build_graph(extract_all, review("high"), review("medium"), summary)
    result = graph.invoke({"contract_path": str(FIXTURE)})
    assert result["report"]["status"] == "needs_human_review"
