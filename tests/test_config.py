from app.config import get_settings


def test_yaml_policy_has_safe_tool_boundaries() -> None:
    settings = get_settings()
    knowledge = settings.mcp.servers["knowledge"]
    ticketing = settings.mcp.servers["ticketing"]
    assert knowledge.tools.allowlist == ["search_policy"]
    assert knowledge.tools.approval_required == []
    assert ticketing.tools.allowlist == ["draft_ticket"]
    assert ticketing.tools.approval_required == ["draft_ticket"]
    for server in settings.mcp.servers.values():
        assert set(server.tools.allowlist).isdisjoint(server.tools.blocklist)


def test_mcp_servers_are_isolated() -> None:
    settings = get_settings()
    knowledge = settings.mcp.servers["knowledge"]
    ticketing = settings.mcp.servers["ticketing"]
    assert knowledge.url != ticketing.url
    assert set(knowledge.tools.allowlist).isdisjoint(ticketing.tools.allowlist)


def test_dummy_key_is_not_usable() -> None:
    assert get_settings().has_usable_api_key is False
