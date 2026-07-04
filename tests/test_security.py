from app.security import RateLimiter, mask_pii


def test_mask_pii() -> None:
    assert mask_pii("a@example.com 090-1234-5678") == "[EMAIL_MASKED] [PHONE_MASKED]"


def test_rate_limiter_fails_closed() -> None:
    limiter = RateLimiter(1)
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is False

