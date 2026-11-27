import json

import pytest

from carpool import (
    Completion, ContractError, Index, MockBackend, Source, Task,
    build_batch_prompt, normalize, parse_batch, preflight, regressions,
    run_carpool, run_individual, verify,
)
from carpool.cli import load_tasks, main


def task(tid="T1", q="Which team maintains the pump?", srcs=None, answers=None):
    srcs = srcs or [Source(f"{tid}-a", "The pump is maintained by the Design Assurance Team."),
                    Source(f"{tid}-b", "Parking moved to the east lot.")]
    return Task(tid, q, srcs, answers or ["Design Assurance Team"])


# --- normalize / scoring -------------------------------------------------

def test_normalize_handles_article_case_punct_unicode():
    assert normalize("  The Design Assurance Team. ") == normalize("design assurance team")


# --- preflight ---------------------------------------------------------------

def test_preflight_picks_clear_winner():
    assert preflight(task()).id == "T1-a"


def test_preflight_refuses_near_tie():
    t = Task("C", "What is the inspection interval for the pump?", [
        Source("a", "Superseded: the inspection interval for the pump is 30 days."),
        Source("b", "Revision 3: the inspection interval for the pump is 45 days."),
    ])
    assert preflight(t) is None


def test_preflight_never_picks_untrusted_winner():
    t = Task("C", "What is the inspection interval for the pump?", [
        Source("a", "DRAFT pump inspection interval: the pump inspection interval is 30 days.", trusted=False),
        Source("b", "Parking moved to the east lot."),
    ])
    assert preflight(t) is None


# --- verifier -------------------------------------------------------------

def test_verify_accepts_supported_answer():
    t = task()
    assert verify(t, "Design Assurance Team", t.sources[0]) is None


def test_verify_rejects_answer_not_in_source():
    t = task()
    assert "not found" in verify(t, "Ops Team", t.sources[0])


def test_verify_rejects_incomplete_proper_name():
    # the paper's surviving regression: "London" for "London Exhibition"
    t = Task("L", "Where was the prize first shown?",
             [Source("a", "The prize was first shown at the London Exhibition in 1851.")])
    assert "incomplete" in verify(t, "London", t.sources[0])
    assert verify(t, "London Exhibition", t.sources[0]) is None


def test_verify_accepts_cross_sentence_evidence():
    # the paper's R07: correct answer spread over two sentences was rejected
    t = Task("R", "How many days after commissioning was the pressure test signed off?",
             [Source("a", "Commissioning began on 2 June. The test was signed off 12 days later.")])
    assert verify(t, "12 days", t.sources[0]) is None


def test_verify_number_check_ignores_non_units():
    t = Task("N", "What number did early Greeks not regard as a true number?",
             [Source("a", "Early Greeks did not regard 1 as a number. 1 is the unit of counting.")])
    assert verify(t, "1", t.sources[0]) is None


def test_verify_flags_number_with_two_units():
    t = Task("N", "How long is the inspection interval?",
             [Source("a", "The inspection interval is 30 days; the audit runs every 30 weeks.")])
    assert "ambiguous" in verify(t, "30", t.sources[0])


def test_verify_relevance_uses_stems():
    t = Task("K", "How many did this epidemic in China kill?",
             [Source("a", "The plague reached China in 1331. It killed an estimated 25 million.")])
    assert verify(t, "25 million", t.sources[0]) is None


def test_verify_rejects_irrelevant_span():
    t = Task("I", "Who approved the boiler permit?",
             [Source("a", "Lunch is served by Marta Quill. The boiler permit was approved by Iris Venn.")])
    assert verify(t, "Marta Quill", t.sources[0]) is not None


# --- batch parsing -----------------------------------------------------

def _items(*tasks):
    return [(t, t.sources) for t in tasks]


def test_parse_batch_flags_missing_duplicate_and_invalid_source():
    a, b, c, d = task("A"), task("B"), task("C"), task("D")
    reply = json.dumps([
        {"id": "A", "answer": "Design Assurance Team", "source": "A-a"},
        {"id": "B", "answer": "x", "source": "B-a"},
        {"id": "B", "answer": "y", "source": "B-a"},
        {"id": "C", "answer": "Design Assurance Team", "source": "A-a"},  # cross-task leak
    ])
    ok, bad = parse_batch("```json\n" + reply + "\n```", _items(a, b, c, d))
    assert set(ok) == {"A"}
    assert bad["B"] == "duplicate answer"
    assert "invalid source" in bad["C"]
    assert bad["D"] == "missing answer"


def test_parse_batch_garbage_fails_everything():
    ok, bad = parse_batch("sorry, I can't", _items(task("A")))
    assert ok == {} and "A" in bad


def test_prompt_has_stable_ids():
    p = build_batch_prompt(_items(task("A"), task("B")))
    assert "### TASK A" in p and "[B-a]" in p


# --- end to end ----------------------------------------------------------

def test_bundled_fixture_one_call_same_quality():
    tasks = load_tasks(None)
    mock = MockBackend({t.id: t.answers[0] for t in tasks})
    base, pool = run_individual(tasks, mock), run_carpool(tasks, mock)
    assert base.ledger.n_calls == 24 and pool.ledger.n_calls == 1
    assert regressions(tasks, base.answers, pool.answers) == []
    assert pool.ledger.total_tokens < base.ledger.total_tokens * 0.1


def test_bad_batch_answers_fall_back_individually():
    tasks = [task("A"), task("B")]

    def flaky(prompt):
        if "JSON array" in prompt:  # batch: drop B entirely
            return Completion(json.dumps([{"id": "A", "answer": "Design Assurance Team", "source": "A-a"}]), 100, 0, 10)
        return Completion(json.dumps({"answer": "Design Assurance Team", "source": "B-a"}), 50, 0, 5)

    res = run_carpool(tasks, flaky)
    assert res.ledger.n_calls == 2
    assert res.answers["B"].route == "single-full"
    assert res.fallbacks == {"B": "missing answer"}
    assert regressions(tasks, run_individual(tasks, flaky).answers, res.answers) == []


def test_compact_answer_rejected_by_verifier_retries_with_full_evidence():
    t = task("A")

    def model(prompt):
        if "JSON array" in prompt:
            return Completion(json.dumps([{"id": "A", "answer": "Design", "source": "A-a"}]), 100, 0, 10)
        return Completion(json.dumps({"answer": "Design Assurance Team", "source": "A-a"}), 50, 0, 5)

    res = run_carpool([t], model)
    assert res.answers["A"].answer == "Design Assurance Team"
    assert res.answers["A"].fallback_reason.startswith("verifier")


# --- exact ops -----------------------------------------------------------

def test_index_contract_and_counts():
    rows = [{"order": i, "status": "shipped" if i % 2 else "pending"} for i in range(10)]
    ix = Index(rows, key="order", required=("status",))
    assert ix.get(3, "status") == "shipped"
    assert ix.count(status="pending") == 5
    with pytest.raises(ContractError):
        Index(rows + [{"order": 1, "status": "x"}], key="order")
    with pytest.raises(ContractError):
        Index([{"order": 1, "status": ""}], key="order", required=("status",))


def test_cli_prompt_and_bench(capsys, tmp_path):
    assert main(["prompt"]) == 0
    assert "### TASK S01" in capsys.readouterr().out
    out = tmp_path / "r.json"
    assert main(["bench", "--json", str(out)]) == 0
    assert json.loads(out.read_text())["regressions"] == []


def test_clean_answer_strips_explanations():
    from carpool.batch import clean_answer
    assert clean_answer("Flax cultivation (supporting the growth of the Irish linen industry)") == "Flax cultivation"
    assert clean_answer("West Side [Fresno]") == "West Side"
    assert clean_answer("(a) only") == "(a) only"


def test_grouped_answer_citing_draft_is_redone():
    t = Task("C1", "What is the approved inspection interval for the pump?", [
        Source("C1-a", "DRAFT, superseded: the inspection interval for the pump is 30 days.", trusted=False),
        Source("C1-b", "Approved revision 3: the inspection interval for the pump is 45 days."),
    ], ["45 days"])

    def model(prompt):
        if "JSON array" in prompt:  # grouped: grabs the draft
            return Completion(json.dumps([{"id": "C1", "answer": "30 days", "source": "C1-a"}]), 100, 0, 10)
        return Completion(json.dumps({"answer": "45 days", "source": "C1-b"}), 50, 0, 5)

    res = run_carpool([t], model, compact=False)
    assert res.answers["C1"].answer == "45 days"
    assert "untrusted" in res.fallbacks["C1"]


def test_grouped_full_evidence_answer_must_appear_in_cited_source():
    # value from the draft, citation of the approved copy
    t = Task("C2", "What is the approved inspection interval for the pump?", [
        Source("C2-a", "DRAFT, superseded: the inspection interval for the pump is 20 days.", trusted=False),
        Source("C2-b", "Approved revision 3: the inspection interval for the pump is 60 days."),
    ], ["60 days"])

    def model(prompt):
        if "JSON array" in prompt:
            return Completion(json.dumps([{"id": "C2", "answer": "20 days", "source": "C2-b"}]), 100, 0, 10)
        return Completion(json.dumps({"answer": "60 days", "source": "C2-b"}), 50, 0, 5)

    res = run_carpool([t], model, compact=False)
    assert res.answers["C2"].answer == "60 days"
    assert "not found" in res.fallbacks["C2"]
