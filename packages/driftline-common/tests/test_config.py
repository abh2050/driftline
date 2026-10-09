from pathlib import Path

import pytest
from pydantic import ValidationError

from driftline_common.config import (
    load_agents_config,
    load_detector_config,
    load_router_config,
)


def test_repo_configs_load() -> None:
    router = load_router_config()
    assert router.active in router.clients
    assert router.escalation_threshold == 0.6
    agents = load_agents_config()
    assert agents.deployment_for("retrieval-specialist") == agents.defaults.deployment
    assert load_detector_config().min_breach_windows >= 1


def test_unknown_active_client_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "router.yaml").write_text(
        "active: missing\nescalation_threshold: 0.6\ndual_route_margin: 0.1\n"
        "clients:\n  rules:\n    kind: rules\n"
    )
    with pytest.raises(ValidationError, match="not in clients"):
        load_router_config(tmp_path)


def test_llm_client_requires_deployment(tmp_path: Path) -> None:
    (tmp_path / "router.yaml").write_text(
        "active: a\nescalation_threshold: 0.6\ndual_route_margin: 0.1\n"
        "clients:\n  a:\n    kind: llm\n"
    )
    with pytest.raises(ValidationError, match="need a deployment"):
        load_router_config(tmp_path)
