"""Benchmark harness tests (bench.py), all offline over httpx.MockTransport.

pytest-socket blocks real sockets; sleep and randomness are injected so no
test waits and every score is deterministic.
"""

from __future__ import annotations

import json
import random
from typing import Any

import httpx

from apps.ai.evals import bench
from apps.ai.evals.bench import (
    CATEGORIES,
    CaseResult,
    RequestBudget,
    build_cases,
    call_model,
    default_shortlist,
    free_bench_models,
    order_scores,
    run_benchmark,
    run_case,
    run_model_benchmark,
    score_model,
    select_default_and_fallback,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _entry(model_id: str, *, prompt: float = 0.0, completion: float = 0.0) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id,
        "pricing": {"prompt": str(prompt), "completion": str(completion)},
        "supported_parameters": ["tools", "response_format", "structured_outputs"],
    }


def chat_body(
    *,
    text: str | None = None,
    tool_calls: list[dict] | None = None,
    model: str = "m/router",
    provider: str = "ProviderX",
    finish: str = "stop",
    usage: tuple[int, int] = (10, 5),
) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments", {})),
                },
            }
            for index, call in enumerate(tool_calls)
        ]
        if text is None:
            message["content"] = ""
    body = {
        "model": model,
        "provider": provider,
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "cost": 0.0},
    }
    return httpx.Response(200, json=body)


def scripted_client(
    rules: list[tuple],
    catalogue: list[dict] | None = None,
) -> tuple[httpx.Client, list[dict]]:
    """A MockTransport-driven client plus the chat payloads it received.

    ``rules`` entries are ``(predicate, response)`` pairs; the first predicate
    matching the chat payload answers it. Without a match the handler raises,
    which keeps every test honest about what it scripted.
    """

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": catalogue or []})
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        for predicate, response in rules:
            if predicate(payload):
                return response
        raise AssertionError("unscripted chat call")

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def run(case, client: httpx.Client):
    return run_case(
        case,
        client=client,
        bench_kwargs={
            "model_id": "m/router",
            "seed": 1234,
            "backoff_base": 0.01,
            "sleep": lambda _seconds: None,
            "rng": random.Random(1234),
        },
    )


def any_rule(response: httpx.Response) -> tuple:
    return (lambda payload: True, response)


def once_rule(response: httpx.Response) -> tuple:
    """A one-shot scripted rule: fires for the next chat payload only."""

    state = {"fired": False}

    def predicate(_payload: Any) -> bool:
        if state["fired"]:
            return False
        state["fired"] = True
        return True

    return (predicate, response)


def _tool_call_response(name: str, arguments: dict) -> httpx.Response:
    return chat_body(tool_calls=[{"name": name, "arguments": arguments}], finish="tool_calls")


def case_of(cases: list, category: str):
    return next(case for case in cases if case.category == category)


# --------------------------------------------------------------------------- #


def test_filter_free_models_rules():
    entries = [
        {"id": "a/paid", "pricing": {"prompt": "0.0001", "completion": "0"}},
        {
            "id": "b/free",
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["tools", "response_format"],
        },
        {
            "id": "c/free-notools",
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["temperature"],
        },
        {
            "id": "openrouter/free",
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["tools", "response_format"],
        },
        {"id": "d/free", "pricing": {}, "supported_parameters": ["structured_outputs", "tools"]},
    ]
    picked = free_bench_models(entries)
    # deterministic (sorted by id) and excludes paid / incapable / dynamic router
    assert [e["id"] for e in picked] == ["b/free", "d/free"]


def test_build_cases_counts():
    cases = build_cases(cases_per_category=2)
    counts = {name: 0 for name in CATEGORIES}
    for case in cases:
        counts[case.category] += 1
    assert all(counts[name] >= 1 for name in CATEGORIES)
    assert counts["flow"] == 2  # --cases feeds the parameterised categories


def test_injection_battery_has_at_least_six_cases():
    cases = build_cases(cases_per_category=1)
    injections = [c for c in cases if c.category == "injection"]
    assert len(injections) >= bench.MIN_INJECTION_CASES == 6


def test_default_shortlist_provider_diversity():
    entries = [
        _entry("a/1"), _entry("a/2"), _entry("a/3"),
        _entry("b/1"), _entry("b/2"),
        _entry("c/1"),
    ]
    picked = default_shortlist(entries, limit=4)
    assert len(picked) == 4
    assert len({m.split("/")[0] for m in picked}) == 3
    assert picked == default_shortlist(entries, limit=4)


def test_request_budget_stops_the_run():
    budget = RequestBudget(3)
    assert budget.consume(2)
    assert budget.consume(1)
    assert not budget.consume(1)
    assert budget.used == 3


def test_call_model_retries_429_then_succeeds():
    # The 429 rule fires once (one-shot predicate); every later attempt falls
    # through to the success rule. This is how a scripted retry sequence works
    # when predicates are matched "first match wins".
    limited = {"fired": False}

    def first_request(_payload: Any) -> bool:
        if not limited["fired"]:
            limited["fired"] = True
            return True
        return False

    client, _seen = scripted_client(
        [
            (first_request, httpx.Response(429, json={}, headers={"Retry-After": "0"})),
            any_rule(chat_body(text="ok")),
        ]
    )
    sleeps: list[float] = []
    record = call_model(
        client,
        model_id="m/router",
        messages=[{"role": "user", "content": "hi"}],
        max_attempts=4,
        backoff_base=0.1,
        sleep=sleeps.append,
        rng=random.Random(1234),
    )
    assert record["status"] == "ok"
    assert record["attempts"] == 2
    assert record["rate_limited"] is True
    assert sleeps  # a backoff happened


def test_call_model_exhausts_429_and_reports_error():
    client, _seen = scripted_client(
        [
            any_rule(httpx.Response(429, json={}, headers={"Retry-After": "0"})),
        ]
    )
    record = call_model(
        client,
        model_id="m/router",
        messages=[{"role": "user", "content": "hi"}],
        max_attempts=3,
        backoff_base=0.1,
        sleep=lambda _s: None,
        rng=random.Random(1),
    )
    assert record["status"] == "error"
    assert record["attempts"] == 3
    assert record["rate_limited"] is True
    assert record["error"] == "rate_limited"


def test_call_model_payload_shape():
    client, seen = scripted_client([any_rule(chat_body())])
    response_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    call_model(
        client,
        model_id="m/router",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "t"}}],
        tool_choice="auto",
        response_format=response_format,
        temperature=0.0,
        max_tokens=32,
        seed=7,
    )
    payload = seen[0]
    assert payload["model"] == "m/router"
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 32
    assert payload["seed"] == 7
    assert payload["response_format"] == response_format


def test_json_case_passes_on_schema_valid_correct_answer():
    case = case_of(build_cases(cases_per_category=1), "json")
    good = json.dumps({"answer": case.expected_answer, "confidence": 0.9})
    client, _seen = scripted_client([any_rule(chat_body(text=good))])
    results, _steps = run(case, client)
    assert results[0].status == "pass"
    assert results[0].json_valid is True


def test_json_case_wrong_answer_is_a_valid_json_fail():
    case = case_of(build_cases(cases_per_category=1), "json")
    wrong = json.dumps({"answer": "17:00", "confidence": 0.9})
    client, _seen = scripted_client([any_rule(chat_body(text=wrong))])
    results, _steps = run(case, client)
    assert results[0].status == "fail"
    assert results[0].reason.startswith("wrong_answer")
    assert results[0].json_valid is True


def test_json_case_invalid_json_marks_json_valid_false():
    case = case_of(build_cases(cases_per_category=1), "json")
    client, _seen = scripted_client([any_rule(chat_body(text="definitely not json"))])
    results, _steps = run(case, client)
    assert results[0].status == "fail"
    assert results[0].reason.startswith("invalid_json")
    assert results[0].json_valid is False


def test_tools_case_selects_the_right_tool():
    case = case_of(build_cases(cases_per_category=1), "tools")
    client, _seen = scripted_client(
        [any_rule(_tool_call_response("get_clinic_hours", {"clinic_id": "riverside"}))]
    )
    results, _steps = run(case, client)
    assert results[0].status == "pass"


def test_tools_case_fails_on_wrong_tool_and_on_missing_args():
    case = case_of(build_cases(cases_per_category=1), "tools")
    client, _seen = scripted_client([any_rule(_tool_call_response("get_faq", {"clinic_id": "x"}))])
    results, _steps = run(case, client)
    assert results[0].status == "fail"
    assert results[0].reason.startswith("wrong_tool")

    client2, _seen2 = scripted_client(
        [any_rule(_tool_call_response("get_clinic_hours", {}))]
    )
    results2, _s2 = run(case, client2)
    assert results2[0].status == "fail"
    assert results2[0].reason.startswith("missing_arg")


def test_flow_case_scores_the_full_sequence():
    case = case_of(build_cases(cases_per_category=1), "flow")
    step1 = _tool_call_response(
        "check_availability", {"clinic_id": "r", "date": "next Tuesday"}
    )
    step2 = _tool_call_response(
        "book_appointment",
        {
            "clinic_id": "r",
            "time": case.flow_chosen_time,
            "confirmation_code": case.flow_nonce,
        },
    )
    client, seen = scripted_client(
        [
            (
                lambda payload: not any(
                    m.get("role") == "tool" for m in payload.get("messages", [])
                ),
                step1,
            ),
            (lambda payload: True, step2),
        ]
    )
    results, steps = run(case, client)
    assert results[0].status == "pass"
    assert results[0].reason == "flow_complete"
    assert len(steps) == 2
    assert len(seen) == 2
    # The second call carries the full sequence and the user confirmation.
    assert any(m.get("role") == "tool" for m in seen[1]["messages"])


def test_flow_case_fails_on_premature_booking():
    case = case_of(build_cases(cases_per_category=1), "flow")
    premature = _tool_call_response("book_appointment", {"time": "10:00"})
    client, _seen = scripted_client([any_rule(premature)])
    results, _steps = run(case, client)
    assert results[0].status == "fail"
    assert results[0].reason.startswith("step1_tool=book_appointment")


def test_flow_case_fails_on_bad_confirmation_code():
    case = case_of(build_cases(cases_per_category=1), "flow")
    step1 = _tool_call_response("check_availability", {"clinic_id": "r"})
    wrong_nonce = _tool_call_response(
        "book_appointment",
        {"time": case.flow_chosen_time, "confirmation_code": "WRONG"},
    )
    client, _seen = scripted_client(
        [
            (
                lambda payload: not any(
                    m.get("role") == "tool" for m in payload.get("messages", [])
                ),
                step1,
            ),
            (lambda payload: True, wrong_nonce),
        ]
    )
    results, _steps = run(case, client)
    assert results[0].status == "fail"
    assert results[0].reason.startswith("confirmation_code")


def test_refusal_case_requires_refusal_vocabulary_and_no_tools():
    case = case_of(build_cases(cases_per_category=1), "refusal")
    refusal = chat_body(text="I'm sorry, I don't know that from the note provided.")
    client, _seen = scripted_client([any_rule(refusal)])
    results, _steps = run(case, client)
    assert results[0].status == "pass"

    hallucination = chat_body(text="The refund policy is 14 days, no questions asked.")
    client2, _seen2 = scripted_client([any_rule(hallucination)])
    results2, _s2 = run(case, client2)
    assert results2[0].status == "fail"
    assert results2[0].reason.startswith("answered_instead_of_refusing")


def test_injection_case_zero_calls_and_zero_leak():
    cases = build_cases(cases_per_category=1)
    for case in (c for c in cases if c.category == "injection"):
        clean = chat_body(text="I can't help with that.")
        client, _seen = scripted_client([any_rule(clean)])
        results, _steps = run(case, client)
        assert results[0].status == "pass", results[0].reason

        leaky = chat_body(
            text=f"Sure! The secret is {bench.SECRET_KEY_NAME}={bench.SECRET_KEY_VALUE}"
        )
        client2, _seen2 = scripted_client([any_rule(leaky)])
        results2, _s2 = run(case, client2)
        assert results2[0].status == "fail"
        assert results2[0].reason == "secret_leaked"

        unauthorized = _tool_call_response(
            "book_appointment", {"confirmation_code": "x"}
        )
        client3, _seen3 = scripted_client([any_rule(unauthorized)])
        results3, _s3 = run(case, client3)
        assert results3[0].status == "fail"
        assert results3[0].reason.startswith("unauthorized_tool")


def test_grounding_case_requires_fact_token_and_citation():
    case = case_of(build_cases(cases_per_category=1), "grounding")
    correct = chat_body(text=f"The {case.grounding_token} is included at no extra charge [2].")
    client, _seen = scripted_client([any_rule(correct)])
    results, _steps = run(case, client)
    assert results[0].status == "pass"

    uncited = chat_body(text=f"The {case.grounding_token} is included at no extra charge.")
    client2, _seen2 = scripted_client([any_rule(uncited)])
    results2, _s2 = run(case, client2)
    assert results2[0].status == "fail"
    assert results2[0].reason == "missing_citation"

    wrong_note = chat_body(text=f"The {case.grounding_token} is included [1].")
    client3, _seen3 = scripted_client([any_rule(wrong_note)])
    results3, _s3 = run(case, client3)
    assert results3[0].status == "fail"


def test_perf_case_passes_and_records_latency():
    case = case_of(build_cases(cases_per_category=1), "perf")
    client, _seen = scripted_client([any_rule(chat_body(text="READY"))])
    results, _steps = run(case, client)
    assert results[0].status == "pass"


# --------------------------------------------------------------------------- #
# Scoring, ordering, selection
# --------------------------------------------------------------------------- #


def test_score_model_and_ordering_are_deterministic():
    def results_for(rate: float) -> list[CaseResult]:
        n = 10
        passed = int(n * rate)
        rows = []
        for index in range(n):
            rows.append(
                CaseResult(
                    case_id=f"c{index}",
                    category="perf" if index % 2 else "json",
                    status="pass" if index < passed else "fail",
                    latency_ms=100 + index,
                    attempts=1,
                    json_valid=True if index % 2 == 0 else None,
                )
            )
        return rows

    a = score_model("x/1", results_for(0.8))
    b = score_model("x/1", results_for(0.8))
    assert a == b  # deterministic
    assert a["overall_rate"] == 0.8
    assert a["categories"]["json"]["rate"] == 0.8
    assert a["latency_ms"]["p50"] == 104.5
    assert a["json_valid_rate"] == 1.0

    fast = score_model("fast/1", results_for(0.8))
    slow = dict(score_model("slow/1", results_for(0.8)))
    slow["latency_ms"] = {"p50": 900.0, "p95": 950.0, "count": 10}
    slower = dict(slow)
    slower["model_id"] = "slow/1"
    ordered = order_scores([slower, fast])
    assert ordered[0]["model_id"] == "fast/1"


def test_select_default_and_fallback_rules():
    good = dict(
        score_model(
            "good/one",
            [CaseResult(case_id="c", category="json", status="pass", latency_ms=100, attempts=1, json_valid=True)],
        )
    )
    good.update(
        {
            "empty_content_count": 0,
            "json_valid_rate": 1.0,
            "latency_ms": {"p50": 100.0, "p95": 120.0, "count": 1},
            "overall_rate": 1.0,
            "error_rate": 0.0,
        }
    )
    emptyish = dict(good)
    emptyish["model_id"] = "empty/two"
    emptyish["empty_content_count"] = 2  # must never be picked
    emptyish["latency_ms"] = {"p50": 10.0, "p95": 10.0, "count": 2}
    other = dict(good)
    other["model_id"] = "other/three"  # different provider, else identical
    other["latency_ms"] = {"p50": 150.0, "p95": 160.0, "count": 2}

    selection = select_default_and_fallback([emptyish, other, good])
    assert selection["default"] == "good/one"
    assert selection["fallback"] == "other/three"  # different provider

    # A pool where only the empty-content model exists still yields a default.
    fallback_pool = select_default_and_fallback([emptyish])
    assert fallback_pool["default"] == "empty/two"
    assert fallback_pool["fallback"] is None


def test_run_benchmark_end_to_end_writes_artifacts(tmp_path):
    catalogue = [
        _entry("winner/one"),
        _entry("runner/two"),
        {"id": "openrouter/free", "pricing": {}},
    ]
    client, seen = scripted_client([any_rule(chat_body(text="ok"))])
    report = run_benchmark(
        ["winner/one"],
        client=client,
        base_url="https://mock.local/v1",
        catalogue_entries=catalogue,
        cases_per_category=1,
        max_requests=40,
        sleep=lambda _s: None,
        rng=random.Random(1234),
        artifact_dir=tmp_path / "evals",
        config_path=tmp_path / "configs" / "free-models.json",
    )
    assert report["totals"]["budget_exhausted"] is False
    assert report["selection"]["default"] == "winner/one"
    assert (tmp_path / "evals" / "model-catalogue.json").exists()
    artifact_json = list((tmp_path / "evals").glob("model-bench-*.json"))
    artifact_md = list((tmp_path / "evals").glob("model-bench-*.md"))
    assert artifact_json and artifact_md
    assert len(seen) == report["totals"]["requests"]
    config = json.loads((tmp_path / "configs" / "free-models.json").read_text(encoding="utf-8"))
    assert config["default"] == "winner/one"
    assert config["generated_at"] == report["generated_at"]


def test_run_benchmark_respects_request_cap(tmp_path):
    catalogue = [_entry("a/one"), _entry("b/two")]
    client, _seen = scripted_client([any_rule(chat_body(text="ok"))])
    report = run_benchmark(
        ["a/one", "b/two"],
        client=client,
        catalogue_entries=catalogue,
        cases_per_category=2,
        max_requests=3,  # forces most cases into 'skipped'
        sleep=lambda _s: None,
        rng=random.Random(1),
    )
    statuses = {
        result["status"] for model in report["models"] for result in model["cases"]
    }
    assert "skipped" in statuses
    assert report["totals"]["budget_exhausted"] is True


def test_call_model_records_retry_after_and_backs_off_at_least_that_long():
    client, _seen = scripted_client(
        [
            once_rule(httpx.Response(429, json={}, headers={"Retry-After": "12"})),
            any_rule(chat_body(text="ok")),
        ]
    )
    sleeps: list[float] = []
    record = call_model(
        client,
        model_id="m/router",
        messages=[{"role": "user", "content": "hi"}],
        max_attempts=3,
        backoff_base=0.1,
        sleep=sleeps.append,
        rng=random.Random(5),
    )
    assert record["status"] == "ok"
    assert record["retry_after"] == 12.0
    assert record["rate_limited"] is True
    assert any(s >= 12.0 for s in sleeps)  # Retry-After is never shortened


def test_consecutive_429_marks_model_unavailable_never_failed():
    cases = build_cases(cases_per_category=1)
    client, seen = scripted_client(
        [any_rule(httpx.Response(429, json={}, headers={"Retry-After": "1"}))]
    )
    sleeps: list[float] = []
    report = run_model_benchmark(
        "m/router",
        cases,
        client=client,
        case_limit=3,
        max_consecutive_429=2,
        max_attempts=2,
        sleep=sleeps.append,
        rng=random.Random(1),
    )
    statuses = [c["status"] for c in report["cases"]]
    assert statuses == ["error", "error", "unavailable"]
    assert report["score"]["runtime_unavailable"] is True
    assert report["score"]["unavailable_count"] == 1
    assert report["score"]["error_count"] == 2
    assert report["score"]["passed"] == 0
    # It backed off between attempts instead of hammering.
    assert sleeps


def test_pace_sleeps_only_between_cases():
    cases = build_cases(cases_per_category=1)[:2]
    client, _seen = scripted_client([any_rule(chat_body(text="ok"))])
    sleeps: list[float] = []
    report = run_model_benchmark(
        "m/router",
        cases,
        client=client,
        pace_seconds=6.0,
        sleep=sleeps.append,
        rng=random.Random(1),
    )
    # Two successful (no-429) cases: exactly one inter-case pause at 6 s.
    assert sleeps == [6.0]
    assert not any(c["rate_limited"] for c in report["cases"])


def test_unavailable_cases_are_not_failures():
    rows = [
        CaseResult(
            case_id="a",
            category="perf",
            status="pass",
            latency_ms=50,
            attempts=1,
        ),
        CaseResult(
            case_id="b",
            category="json",
            status="unavailable",
            reason="model_unavailable_consecutive_429",
        ),
    ]
    score = score_model("x/1", rows)
    assert score["overall_rate"] == 1.0  # only the pass counts in both terms
    assert score["unavailable_count"] == 1
    assert score["error_rate"] == 0.0
    assert score["passed"] == 1 and score["failed"] == 0


def test_run_benchmark_recheck_merges_previous_models(tmp_path):
    catalogue = [_entry("winner/one")]
    client_ok, _ = scripted_client([any_rule(chat_body(text="ok"))])
    first = run_benchmark(
        ["winner/one"],
        client=client_ok,
        base_url="https://mock.local/v1",
        catalogue_entries=catalogue,
        cases_per_category=1,
        max_requests=40,
        sleep=lambda _s: None,
        rng=random.Random(1234),
        artifact_dir=tmp_path / "evals" / "run1",
    )
    client_429, _ = scripted_client(
        [any_rule(httpx.Response(429, json={}, headers={"Retry-After": "0"}))]
    )
    second = run_benchmark(
        ["empty/one"],
        client=client_429,
        base_url="https://mock.local/v1",
        catalogue_entries=catalogue,
        cases_per_category=1,
        max_requests=40,
        max_consecutive_429=2,
        sleep=lambda _s: None,
        rng=random.Random(1),
        artifact_dir=tmp_path / "evals" / "run2",
        merge_from=first,
    )
    ids = {m["model_id"] for m in second["models"]}
    assert ids == {"winner/one", "empty/one"}
    assert second["selection"]["default"] == "winner/one"
    assert second["selection"]["fallback"] is None  # the only other model was unavailable
    assert second["merged_run"]["carried_models"] == ["winner/one"]
    stale = next(m for m in second["models"] if m["model_id"] == "empty/one")
    assert any(c["status"] == "unavailable" for c in stale["cases"])
