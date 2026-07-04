from app.auth import AuthManager
from app.config import get_settings


def test_local_login_and_session_round_trip() -> None:
    auth = AuthManager(get_settings())
    user = auth.authenticate("employee@example.com", "DemoPass123!")
    assert user is not None
    assert user.role == "employee"
    assert user.department == "sales"
    token, csrf = auth.issue_session(user)
    restored, restored_csrf = auth.read_session(token)
    assert restored.id == user.id
    assert restored_csrf == csrf


def test_wrong_password_is_rejected() -> None:
    auth = AuthManager(get_settings())
    assert auth.authenticate("employee@example.com", "wrong-password") is None


def test_mcp_token_carries_server_verified_attributes() -> None:
    auth = AuthManager(get_settings())
    user = auth.authenticate("finance@example.com", "FinancePass123!")
    assert user is not None
    token = auth.issue_mcp_token(user.model_dump())
    claims = auth.verify_mcp_token(token)
    assert claims["tenant_id"] == "acme-jp"
    assert claims["department"] == "finance"
    assert "policy:finance" in claims["scopes"]


def test_scope_check_fails_closed() -> None:
    auth = AuthManager(get_settings())
    user = auth.authenticate("employee@example.com", "DemoPass123!")
    assert user is not None
    try:
        auth.require_scope(user.model_dump(), "audit:read")
    except PermissionError:
        pass
    else:
        raise AssertionError("missing scope must be rejected")

