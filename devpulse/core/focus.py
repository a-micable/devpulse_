# devpulse/core/focus.py
# Focus score computation.
# Focus score is a 0.0–1.0 value derived from:
#   - heartbeat regularity (gap variance)
#   - save event density
#   - idle time ratio
#   - session continuity (pause count)

from __future__ import annotations

import math
import logging
from typing import List, Optional

log = logging.getLogger(__name__)

# Tuning weights — must sum to 1.0
_W_HEARTBEAT   = 0.35
_W_SAVE        = 0.25
_W_IDLE        = 0.25
_W_CONTINUITY  = 0.15

# Thresholds
_IDEAL_HB_GAP_S   = 30    # ideal heartbeat interval
_MAX_HB_GAP_S     = 300   # gaps above this count as idle
_IDEAL_SAVE_RATE  = 0.05  # saves per second (1 save per 20s = fully focused)


def compute_focus_score(
    duration_s:       int,
    heartbeat_count:  int,
    save_events:      int,
    idle_s:           int,
    pause_count:      int,
    heartbeat_gaps:   Optional[List[float]] = None,
) -> float:
    """
    Compute a focus score in [0.0, 1.0].

    Args:
        duration_s:      total session wall-clock seconds
        heartbeat_count: number of heartbeats recorded
        save_events:     number of file-save events
        idle_s:          seconds classified as idle
        pause_count:     number of manual pauses
        heartbeat_gaps:  optional list of gap durations in seconds

    Returns:
        float in [0.0, 1.0]
    """
    if duration_s <= 0:
        return 0.0

    hb_score   = _heartbeat_score(duration_s, heartbeat_count, heartbeat_gaps)
    save_score = _save_score(duration_s, save_events)
    idle_score = _idle_score(duration_s, idle_s)
    cont_score = _continuity_score(pause_count)

    raw = (
        _W_HEARTBEAT  * hb_score  +
        _W_SAVE       * save_score +
        _W_IDLE       * idle_score +
        _W_CONTINUITY * cont_score
    )

    return round(min(1.0, max(0.0, raw)), 4)


def focus_label(score: float) -> str:
    if score >= 0.80:
        return "deep"
    if score >= 0.55:
        return "focused"
    if score >= 0.30:
        return "light"
    return "distracted"


# ---------------------------------------------------------------------------
# Sub-scores
# ---------------------------------------------------------------------------

def _heartbeat_score(duration_s: int, count: int, gaps: Optional[List[float]]) -> float:
    if count == 0:
        return 0.0

    expected = duration_s / _IDEAL_HB_GAP_S
    density  = min(1.0, count / expected) if expected > 0 else 0.0

    if not gaps or len(gaps) < 2:
        return density

    # Penalise high variance in gap lengths
    mean = sum(gaps) / len(gaps)
    if mean == 0:
        return density
    variance = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv       = math.sqrt(variance) / mean          # coefficient of variation
    regularity = max(0.0, 1.0 - min(cv, 2.0) / 2.0)

    return 0.6 * density + 0.4 * regularity


def _save_score(duration_s: int, save_events: int) -> float:
    if duration_s == 0:
        return 0.0
    rate = save_events / duration_s
    # sigmoid-like: full score at ideal rate, tapers off
    ratio = rate / _IDEAL_SAVE_RATE if _IDEAL_SAVE_RATE > 0 else 0
    return min(1.0, ratio ** 0.5)


def _idle_score(duration_s: int, idle_s: int) -> float:
    if duration_s == 0:
        return 0.0
    idle_ratio = min(1.0, idle_s / duration_s)
    return 1.0 - idle_ratio


def _continuity_score(pause_count: int) -> float:
    # Each pause reduces score: 0 pauses = 1.0, decays with sqrt
    if pause_count == 0:
        return 1.0
    return max(0.0, 1.0 - 0.25 * math.sqrt(pause_count))