"""Tests for VLM helper utilities."""

from __future__ import annotations

from doc_convert.vlm import is_mps_float64_error


def test_is_mps_float64_error_positive() -> None:
    assert is_mps_float64_error(RuntimeError("MPS does not support float64"))
    assert is_mps_float64_error(TypeError("Cannot convert: mps doesn't support this"))


def test_is_mps_float64_error_negative() -> None:
    assert not is_mps_float64_error(RuntimeError("CUDA out of memory"))
    assert not is_mps_float64_error(ValueError("float64 overflow on cpu"))
