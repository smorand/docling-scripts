"""Tests for audio preparation: split planning and overlap boundaries."""

from __future__ import annotations

from itertools import pairwise

from audio_prep import (
    DURATION_LIMIT_SECONDS,
    GOOGLE_DURATION_LIMIT_SECONDS,
    OVERLAP_SECONDS,
    SIZE_LIMIT_MB,
    plan_parts,
)

# A duration comfortably under the ceiling, used when only size should drive splits.
_SHORT = 600.0


def test_plan_parts_no_split_when_under_limit() -> None:
    assert plan_parts(_SHORT, SIZE_LIMIT_MB - 10, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_no_split_at_exactly_limit() -> None:
    assert plan_parts(_SHORT, SIZE_LIMIT_MB, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_no_split_zero_duration() -> None:
    assert plan_parts(0, 999, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_splits_on_duration_even_when_small() -> None:
    # The 524 bug: the real 2h05 recording is only ~20 MB (well under the size
    # limit) but must still split because it exceeds the per-request duration
    # ceiling. 7498 s / 1800 s -> ceil = 5 parts (explicit ceiling to pin math).
    spans = plan_parts(7498, 20, limit_mb=SIZE_LIMIT_MB, duration_limit_s=1800)
    assert len(spans) == 5
    assert spans[0][0] == 0.0
    assert spans[-1][1] == 7498
    for prev, nxt in pairwise(spans):
        assert nxt[0] < prev[1]  # overlap, no coverage gap


def test_plan_parts_no_split_under_duration_ceiling() -> None:
    assert plan_parts(DURATION_LIMIT_SECONDS, 10, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_default_ceiling_keeps_parts_within_limit() -> None:
    # With the production default, every part span stays within the ceiling: the
    # ReadTimeout bug came from ~29 min parts, so no part may exceed the ceiling.
    spans = plan_parts(7498, 18, limit_mb=SIZE_LIMIT_MB)
    assert spans  # a 2 h recording splits
    for start, end in spans:
        assert end - start <= DURATION_LIMIT_SECONDS + 1  # +1 for float rounding


def test_plan_parts_google_ceiling_still_splits_long_audio() -> None:
    # google/ has no gateway, but the model malforms on a huge single shot, so a
    # 2 h recording must still split under the (looser) google ceiling.
    spans = plan_parts(7498, 18, limit_mb=float("inf"), duration_limit_s=GOOGLE_DURATION_LIMIT_SECONDS)
    assert len(spans) == 5  # ceil(7498 / 1800)
    for start, end in spans:
        assert end - start <= GOOGLE_DURATION_LIMIT_SECONDS + 1


def test_plan_parts_uses_max_of_size_and_duration() -> None:
    # 160 MB -> 4 parts by size; 7498 s / 1800 s -> 5 parts by duration.
    # The larger count wins.
    spans = plan_parts(7498, 160, limit_mb=50, duration_limit_s=1800)
    assert len(spans) == 5


def test_plan_parts_two_parts_with_overlap() -> None:
    # 80 MB / 50 MB limit -> ceil = 2 parts, seg = 3600 s each. Duration ceiling
    # disabled to isolate the size-driven split.
    spans = plan_parts(7200, 80, limit_mb=50, duration_limit_s=float("inf"), overlap_s=60)
    assert len(spans) == 2
    (s0, e0), (s1, e1) = spans
    assert s0 == 0.0
    assert e0 == 3600
    assert s1 == 3600 - 60  # part 2 starts one minute early
    assert e1 == 7200
    assert round(e0 - s1) == 60  # exactly one minute of overlap


def test_plan_parts_covers_full_duration_and_overlaps() -> None:
    # ceil(160/50) = 4; duration ceiling disabled to isolate the size-driven split.
    spans = plan_parts(10000, 160, limit_mb=50, duration_limit_s=float("inf"), overlap_s=OVERLAP_SECONDS)
    assert len(spans) == 4
    assert spans[0][0] == 0.0
    assert spans[-1][1] == 10000
    for prev, nxt in pairwise(spans):
        assert nxt[0] < prev[1]  # consecutive parts overlap, so no gap in coverage
        assert round(prev[1] - nxt[0]) == OVERLAP_SECONDS
