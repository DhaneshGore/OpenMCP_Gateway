from __future__ import annotations

import pytest

from openmcp_gateway.routing.namespace import qualify, split


@pytest.mark.parametrize(
    ("server", "tool"),
    [("github", "create_issue"), ("a", "x"), ("my-srv", "tool__with__underscores"), ("s1", "_leading")],
)
def test_round_trip(server: str, tool: str) -> None:
    assert split(qualify(server, tool)) == (server, tool)


@pytest.mark.parametrize("name", ["", "plain", "__tool", "srv__", "Bad__tool", "a_b__tool", "-x__t"])
def test_rejects_non_gateway_names(name: str) -> None:
    assert split(name) is None
