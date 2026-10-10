from __future__ import annotations

import pytest

from kql_tool.queries import QueryError, load_all

QUERIES = load_all()


def test_all_queries_parse_and_render_with_defaults() -> None:
    assert {"patient_rag_requests", "agent_health", "recent_decisions", "trace_spans"} <= set(
        QUERIES
    )
    for name, q in QUERIES.items():
        if any(p.default is None for p in q.params):
            continue
        assert "{" not in q.render({}), name


@pytest.mark.parametrize("bad", ["60; drop table", "x", -5, 999999])
def test_int_params_reject_injection_and_range(bad: object) -> None:
    with pytest.raises(QueryError):
        QUERIES["patient_rag_requests"].render({"minutes": bad})


def test_enum_and_hex_validation() -> None:
    with pytest.raises(QueryError):
        QUERIES["recent_decisions"].render({"decision_type": 'D3" | take 1'})
    with pytest.raises(QueryError):
        QUERIES["trace_spans"].render({"trace_id": "abc"})
    assert "0" * 32 in QUERIES["trace_spans"].render({"trace_id": "0" * 32})


def test_unknown_parameter_rejected() -> None:
    with pytest.raises(QueryError):
        QUERIES["agent_health"].render({"minutes": 15, "extra": 1})
