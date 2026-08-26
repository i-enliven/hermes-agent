"""Tests for gateway.memory_status — the /api/status memory rollup (NS-656)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gateway.memory_status import classify_pressure
from gateway.shutdown_watchdog import get_loop_heartbeat_path
from gateway.lifecycle_ledger import get_lifecycle_sentinel_path

_NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def _write_heartbeat(
    home: Path,
    *,
    updated_at: datetime = _NOW,
    mem: dict | None = None,
) -> None:
    path = get_loop_heartbeat_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": 12345,
        "updated_at": updated_at.isoformat(),
        "monotonic": 1.0,
    }
    if mem is not None:
        payload["mem"] = mem
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_sentinel(home: Path, payload: dict) -> None:
    path = get_lifecycle_sentinel_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestClassifyPressure:
    def test_plentiful_memory_is_ok(self) -> None:
        # 1 GiB available of 2 GiB total.
        assert classify_pressure(1024 * 1024, 2048 * 1024) == "ok"

    def test_low_absolute_available_is_critical(self) -> None:
        # 32 MiB available — below the 64 MiB floor regardless of total.
        assert classify_pressure(32 * 1024, 8 * 1024 * 1024) == "critical"

    def test_low_fraction_is_critical(self) -> None:
        # 300 MiB available of 8 GiB ≈ 3.7% < 5%.
        assert classify_pressure(300 * 1024, 8 * 1024 * 1024) == "critical"

    def test_elevated_band(self) -> None:
        # 100 MiB available of 1 GiB ≈ 9.8% — above critical, below elevated
        # thresholds (128 MiB / 15%).
        assert classify_pressure(100 * 1024, 1024 * 1024) == "elevated"

    def test_missing_sample_is_unknown(self) -> None:
        assert classify_pressure(None, None) == "unknown"

    def test_bool_is_not_an_int(self) -> None:
        # True == 1 in Python — must not classify as "1 KiB available".
        assert classify_pressure(True, 2048 * 1024) == "unknown"

    def test_absolute_floor_works_without_total(self) -> None:
        assert classify_pressure(32 * 1024, None) == "critical"
        # 1 GiB available, unknown total: passes both absolute floors → ok.
        assert classify_pressure(1024 * 1024, None) == "ok"
