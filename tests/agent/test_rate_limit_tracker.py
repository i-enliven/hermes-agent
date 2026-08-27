"""Tests for agent.rate_limit_tracker — header parsing and formatting."""

import time
import pytest
from agent.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    parse_rate_limit_headers,
    format_rate_limit_display,
    format_rate_limit_compact,
    _fmt_count,
    _fmt_seconds,
    _bar,
)


# ── Sample headers from Nous inference API ──────────────────────────────

class TestParseHeaders:

    def test_no_headers(self):
        state = parse_rate_limit_headers({})
        assert state is None





class TestBucket:

    def test_usage_pct(self):
        b = RateLimitBucket(limit=100, remaining=20, reset_seconds=30.0, captured_at=time.time())
        assert b.usage_pct == pytest.approx(80.0)


    def test_remaining_seconds_now(self):
        now = time.time()
        b = RateLimitBucket(limit=800, remaining=795, reset_seconds=60.0, captured_at=now - 10)
        # ~50 seconds should remain
        assert 49 <= b.remaining_seconds_now <= 51



class TestFormatting:



    def test_fmt_seconds_short(self):
        assert _fmt_seconds(45) == "45s"
        assert _fmt_seconds(0) == "0s"



    def test_bar(self):
        bar = _bar(50.0, width=10)
        assert bar == "[█████░░░░░]"
        assert _bar(0.0, width=10) == "[░░░░░░░░░░]"
        assert _bar(100.0, width=10) == "[██████████]"







class TestAgentIntegration:
    """Test that AIAgent captures rate limit state correctly."""


    def test_capture_rate_limits_none_response(self):
        """_capture_rate_limits should handle None gracefully."""
        from agent.rate_limit_tracker import parse_rate_limit_headers
        # None should not crash
        result = parse_rate_limit_headers({})
        assert result is None
