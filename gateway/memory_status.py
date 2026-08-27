"""Memory status rollup for ``/api/status`` (NS-656).

The gateway already *produces* every memory-pressure signal a user would
want to know about, but all of it dies in log files:

* :func:`gateway.shutdown_watchdog.write_loop_heartbeat` embeds a
  :func:`gateway.lifecycle_ledger.sample_memory` snapshot (gateway RSS +
  system MemAvailable/MemTotal + swap) in ``state/gateway.heartbeat``
  every 30 seconds.
* :func:`gateway.lifecycle_ledger.record_startup` detects an unclean
  previous death and flags ``suspected_oom`` — but only into
  ``gateway-exit-diag.log`` and a WARNING line.
* ``gateway/agent_cache_pressure.py`` evicts transcripts under pressure,
  again log-only.

So a hosted agent can be OOM-killed hourly (the BlueAtlas incident,
NS-608) while its dashboard and the NAS agent card both look perfectly
healthy.  This module is the read side that closes the gap: it distills
the *already-persisted* heartbeat + lifecycle sentinel into a compact,
public-safe block that ``/api/status`` can serve to the dashboard SPA
and the NAS availability sweep — no new sampling, no IPC with the
gateway process, just two small file reads.

Public-safety note: ``/api/status`` is an unauthenticated liveness probe
(``PUBLIC_API_PATHS``), which is exactly why NAS can consume it.  This
block therefore carries only coarse numbers (MB granularity), enums, and
booleans — the same disclosure class as the existing ``active_agents``
count (which was added for the same
NAS-sweep audience).

Everything here is best-effort and read-only: a missing/corrupt file
degrades to ``pressure="unknown"`` rather than raising into the status
endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Pressure thresholds on system MemAvailable.  ``critical`` deliberately
# mirrors the lifecycle ledger's OOM-suspicion heuristics
# (:data:`gateway.lifecycle_ledger._LOW_MEM_AVAILABLE_KIB` /
# ``_LOW_MEM_AVAILABLE_FRACTION``): if a memory level would make a
# subsequent unclean death "suspected OOM", the user should already have
# been warned at that level while the process was still alive.
_CRITICAL_AVAILABLE_KIB = 64 * 1024  # < 64 MiB available
_CRITICAL_AVAILABLE_FRACTION = 0.05  # < 5% of MemTotal
_ELEVATED_AVAILABLE_KIB = 128 * 1024  # < 128 MiB available
_ELEVATED_AVAILABLE_FRACTION = 0.15  # < 15% of MemTotal

# A heartbeat older than this no longer describes the present.  The writer
# cadence is 30s (DEFAULT_HEARTBEAT_INTERVAL_S); 150s of slack tolerates a
# briefly stalled loop without letting a long-dead gateway's last sample
# masquerade as current pressure.
_HEARTBEAT_FRESH_TTL_S = 150.0

_KIB_PER_MB = 1024


def _mb(kib: Any) -> Optional[int]:
    if isinstance(kib, bool) or not isinstance(kib, int) or kib < 0:
        return None
    return kib // _KIB_PER_MB


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_pressure(
    available_kib: Any, total_kib: Any
) -> str:
    """Map a MemAvailable/MemTotal pair to ``ok``/``elevated``/``critical``.

    ``unknown`` when the sample is missing or malformed — the caller must
    not treat "we could not read it" as "memory is fine".
    """
    if (
        isinstance(available_kib, bool)
        or not isinstance(available_kib, int)
        or available_kib < 0
    ):
        return "unknown"
    fraction: Optional[float] = None
    if (
        not isinstance(total_kib, bool)
        and isinstance(total_kib, int)
        and total_kib > 0
    ):
        fraction = available_kib / total_kib
    if available_kib < _CRITICAL_AVAILABLE_KIB or (
        fraction is not None and fraction < _CRITICAL_AVAILABLE_FRACTION
    ):
        return "critical"
    if available_kib < _ELEVATED_AVAILABLE_KIB or (
        fraction is not None and fraction < _ELEVATED_AVAILABLE_FRACTION
    ):
        return "elevated"
    return "ok"

