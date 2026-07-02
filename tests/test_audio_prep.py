"""Tests for audio preparation: split planning and overlap boundaries."""

from __future__ import annotations

from itertools import pairwise

from audio_prep import OVERLAP_SECONDS, SIZE_LIMIT_MB, plan_parts


def test_plan_parts_no_split_when_under_limit() -> None:
    assert plan_parts(3600, SIZE_LIMIT_MB - 10, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_no_split_at_exactly_limit() -> None:
    assert plan_parts(3600, SIZE_LIMIT_MB, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_no_split_zero_duration() -> None:
    assert plan_parts(0, 999, limit_mb=SIZE_LIMIT_MB) == []


def test_plan_parts_two_parts_with_overlap() -> None:
    # 80 MB / 50 MB limit -> ceil = 2 parts, seg = 3600 s each.
    spans = plan_parts(7200, 80, limit_mb=50, overlap_s=60)
    assert len(spans) == 2
    (s0, e0), (s1, e1) = spans
    assert s0 == 0.0
    assert e0 == 3600
    assert s1 == 3600 - 60  # part 2 starts one minute early
    assert e1 == 7200
    assert round(e0 - s1) == 60  # exactly one minute of overlap


def test_plan_parts_covers_full_duration_and_overlaps() -> None:
    spans = plan_parts(10000, 160, limit_mb=50, overlap_s=OVERLAP_SECONDS)  # ceil(160/50) = 4
    assert len(spans) == 4
    assert spans[0][0] == 0.0
    assert spans[-1][1] == 10000
    for prev, nxt in pairwise(spans):
        assert nxt[0] < prev[1]  # consecutive parts overlap, so no gap in coverage
        assert round(prev[1] - nxt[0]) == OVERLAP_SECONDS
