"""Tests for the Stage B1 figure classifier (pure logic, model mocked out).

The real EfficientNet model is never loaded here: these tests pin the decision
logic (decorative categories, confidence threshold, batching, fail-open) so the
suite stays offline and fast. Real-model behavior is validated by E2E runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from doc_convert import figure_classifier as fc
from doc_convert.base import ConvertOptions
from doc_convert.converters.image import ImageConverter


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """Never let one test leak a loaded/failed model state into the next."""
    fc.reset_cache()


def _make_png(path: Path, width: int = 200, height: int = 200) -> Path:
    Image.new("RGB", (width, height), (10, 120, 200)).save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# FigureClass.is_decorative: the actual drop decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(fc.DECORATIVE_CATEGORIES))
def test_decorative_mass_above_threshold_is_dropped(label: str) -> None:
    assert fc.FigureClass(label=label, confidence=0.95, decorative_mass=0.95).is_decorative


@pytest.mark.parametrize("label", sorted(fc.DECORATIVE_CATEGORIES))
def test_decorative_mass_below_threshold_is_kept(label: str) -> None:
    """A hesitating classifier must not cause a drop."""
    assert not fc.FigureClass(label=label, confidence=0.5, decorative_mass=fc.MIN_DECORATIVE_MASS - 0.01).is_decorative


def test_exactly_at_threshold_is_dropped() -> None:
    assert fc.FigureClass(label="logo", confidence=0.5, decorative_mass=fc.MIN_DECORATIVE_MASS).is_decorative


def test_split_mass_between_logo_and_icon_still_drops() -> None:
    """The rule this replaced: the IBM DB2 wordmark scored logo 0.35 / icon 0.34,
    so a top-1 threshold at 0.8 kept it even though the classifier was 69% sure
    it was decorative. Summing the decorative categories is the whole point."""
    verdict = fc.FigureClass(label="logo", confidence=0.35, decorative_mass=0.85)
    assert verdict.is_decorative, "a confident sum must drop even with a timid top-1"


@pytest.mark.parametrize(
    "label",
    ["photograph", "bar_chart", "line_chart", "pie_chart", "table", "flow_chart", "screenshot_from_computer", "other"],
)
def test_content_labels_are_never_dropped(label: str) -> None:
    """A content top-1 leaves little mass for the decorative classes, so the sum
    stays below the threshold and the figure is captioned."""
    assert not fc.FigureClass(label=label, confidence=1.0, decorative_mass=0.0).is_decorative


def test_measured_separation_band_is_respected() -> None:
    """On the deck this threshold was set from, content misread as decorative
    topped out at 0.658 and vendor logos started at 0.575. The threshold must sit
    above the content band so no measured content item would be dropped."""
    assert fc.MIN_DECORATIVE_MASS > 0.658
    assert not fc.FigureClass(label="logo", confidence=0.41, decorative_mass=0.658).is_decorative


def test_unknown_is_never_decorative() -> None:
    """The failure verdict must always mean 'keep and caption'."""
    assert not fc.UNKNOWN.is_decorative
    assert fc.UNKNOWN.decorative_mass == 0.0


def test_other_category_is_not_decorative() -> None:
    """'other' is the classifier's weakest class; it must never trigger a drop."""
    assert "other" not in fc.DECORATIVE_CATEGORIES


# ---------------------------------------------------------------------------
# Batching and fail-open behaviour of the public entry point
# ---------------------------------------------------------------------------


def test_classify_empty_list_never_loads_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """No figures means no model load at all (keeps zero-figure docs free)."""

    def boom() -> None:
        raise AssertionError("_load must not be called for an empty input")

    monkeypatch.setattr(fc, "_load", boom)
    assert fc.classify_figures([]) == []


def test_classify_returns_unknown_when_model_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Offline / missing model: every figure comes back UNKNOWN, so nothing drops."""
    monkeypatch.setattr(fc, "_load", lambda: None)
    paths = [_make_png(tmp_path / "a.png"), _make_png(tmp_path / "b.png")]
    verdicts = fc.classify_figures(paths)
    assert verdicts == [fc.UNKNOWN, fc.UNKNOWN]
    assert not any(v.is_decorative for v in verdicts)


def test_load_failure_is_remembered_and_warned_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken load must be attempted (and warned about) once, not per batch."""
    calls = {"n": 0}

    def fake_resolve() -> tuple[str, str]:
        calls["n"] += 1
        raise RuntimeError("no torch here")

    monkeypatch.setattr(fc, "_resolve_model_ref", fake_resolve)
    with caplog.at_level("WARNING", logger="doc_convert.figure_classifier"):
        assert fc._load() is None
        assert fc._load() is None

    assert calls["n"] == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "figure classifier unavailable" in warnings[0].getMessage()


def test_classify_batches_and_preserves_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verdicts must line up 1:1 with the input paths across batch boundaries."""
    paths = [_make_png(tmp_path / f"f{i}.png") for i in range(20)]
    state = fc._Loaded(processor=object(), model=object(), device="cpu", torch=object())
    monkeypatch.setattr(fc, "_load", lambda: state)

    seen_batches: list[int] = []

    def fake_batch(_state: fc._Loaded, chunk: list[Path]) -> list[fc.FigureClass]:
        seen_batches.append(len(chunk))
        return [fc.FigureClass(label=p.stem, confidence=1.0) for p in chunk]

    monkeypatch.setattr(fc, "_classify_batch", fake_batch)
    verdicts = fc.classify_figures(paths)

    assert [v.label for v in verdicts] == [p.stem for p in paths]
    # CPU batch size is 8: 20 images -> 8 + 8 + 4
    assert seen_batches == [8, 8, 4]


def test_classify_batch_keeps_unreadable_images(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A corrupt file yields UNKNOWN for that slot, valid neighbours still classify."""
    good = _make_png(tmp_path / "good.png")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"definitely not an image")
    state = fc._Loaded(processor=object(), model=object(), device="cpu", torch=object())

    monkeypatch.setattr(
        fc,
        "_forward",
        lambda _state, images: [fc.FigureClass(label="logo", confidence=0.99) for _ in images],
    )
    verdicts = fc._classify_batch(state, [broken, good])

    assert verdicts[0] == fc.UNKNOWN
    assert verdicts[1].label == "logo"


def test_classify_batch_keeps_everything_when_forward_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A torch-level failure must degrade to 'caption everything', not to a drop."""
    paths = [_make_png(tmp_path / "a.png"), _make_png(tmp_path / "b.png")]
    state = fc._Loaded(processor=object(), model=object(), device="cpu", torch=object())

    def boom(_state: fc._Loaded, _images: list[object]) -> list[fc.FigureClass]:
        raise RuntimeError("mps exploded")

    monkeypatch.setattr(fc, "_forward", boom)
    assert fc._classify_batch(state, paths) == [fc.UNKNOWN, fc.UNKNOWN]


def test_resolve_model_ref_pins_the_validated_revision() -> None:
    """Supply-chain guard (CWE-494): the model we validated is fetched by commit,
    so an upstream force-push to `main` cannot swap the weights silently."""
    repo_id, revision = fc._resolve_model_ref()
    assert repo_id == fc._FALLBACK_REPO_ID
    assert revision == fc._VALIDATED_REVISION
    assert len(revision) == 40, "a real commit SHA, not a branch name"


def test_resolve_model_ref_falls_back_on_docling_api_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """If docling's spec cannot be read we still know which model to ask for."""
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "docling.datamodel":
            raise ImportError("gone")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    repo_id, revision = fc._resolve_model_ref()
    assert repo_id == fc._FALLBACK_REPO_ID
    assert revision == fc._VALIDATED_REVISION


def test_resolve_model_ref_follows_docling_when_repo_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a docling model bump we must NOT force our SHA onto another repo: it
    would 404 and silently disable the filter. Follow docling's revision."""

    class _Spec:
        @staticmethod
        def get_repo_id(_engine: object) -> str:
            return "docling-project/DocumentFigureClassifier-v9.9"

        @staticmethod
        def get_revision(_engine: object) -> str:
            return "abc123"

    class _Preset:
        model_spec = _Spec()
        default_engine_type = object()

    from docling.datamodel import stage_model_specs  # noqa: PLC0415

    monkeypatch.setattr(stage_model_specs, "IMAGE_CLASSIFICATION_DOCUMENT_FIGURE", _Preset())

    repo_id, revision = fc._resolve_model_ref()
    assert repo_id == "docling-project/DocumentFigureClassifier-v9.9"
    assert revision == "abc123"


# ---------------------------------------------------------------------------
# BaseConverter.filter_figures_by_class: cascade wiring
# ---------------------------------------------------------------------------


def _converter(tmp_path: Path, *, caption_filter: bool = True) -> ImageConverter:
    options = ConvertOptions(output_dir=tmp_path / "out", caption_filter=caption_filter)
    return ImageConverter(tmp_path / "unused.png", options)


def test_filter_by_class_drops_decorative_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    logo = _make_png(tmp_path / "logo.png")
    chart = _make_png(tmp_path / "chart.png")
    verdicts = {
        logo: fc.FigureClass(label="logo", confidence=0.97, decorative_mass=0.99),
        chart: fc.FigureClass(label="bar_chart", confidence=0.97, decorative_mass=0.01),
    }
    monkeypatch.setattr(
        "doc_convert.figure_classifier.classify_figures",
        lambda paths: [verdicts[p] for p in paths],
    )

    converter = _converter(tmp_path)
    figure_map, image_paths, item_refs = converter.filter_figures_by_class(
        {"#0": "figures/logo.png", "#1": "figures/chart.png"},
        [logo, chart],
        ["#0", "#1"],
    )

    assert item_refs == ["#1"]
    assert image_paths == [chart]
    assert figure_map == {"#1": "figures/chart.png"}


def test_filter_by_class_classifies_each_file_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two refs pointing at one file (post exact-dedup) cost one classification."""
    shared = _make_png(tmp_path / "shared.png")
    seen: list[list[Path]] = []

    def fake_classify(paths: list[Path]) -> list[fc.FigureClass]:
        seen.append(list(paths))
        return [fc.FigureClass(label="icon", confidence=0.99, decorative_mass=0.99) for _ in paths]

    monkeypatch.setattr("doc_convert.figure_classifier.classify_figures", fake_classify)

    converter = _converter(tmp_path)
    figure_map, image_paths, item_refs = converter.filter_figures_by_class(
        {"#0": "figures/shared.png", "#1": "figures/shared.png"},
        [shared, shared],
        ["#0", "#1"],
    )

    assert seen == [[shared]]  # classified once, not twice
    assert (figure_map, image_paths, item_refs) == ({}, [], [])  # both refs dropped


def test_filter_by_class_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--no-caption-filter must not even call the classifier."""

    def boom(_paths: list[Path]) -> list[fc.FigureClass]:
        raise AssertionError("classifier must not run when the filter is disabled")

    monkeypatch.setattr("doc_convert.figure_classifier.classify_figures", boom)
    logo = _make_png(tmp_path / "logo.png")

    converter = _converter(tmp_path, caption_filter=False)
    result = converter.filter_figures_by_class({"#0": "figures/logo.png"}, [logo], ["#0"])

    assert result == ({"#0": "figures/logo.png"}, [logo], ["#0"])


def test_filter_figures_runs_size_floor_before_classifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stage A must shrink the batch handed to Stage B1 (cheap gate first)."""
    tiny = _make_png(tmp_path / "tiny.png", 20, 20)
    big = _make_png(tmp_path / "big.png", 400, 400)
    classified: list[list[Path]] = []

    def fake_classify(paths: list[Path]) -> list[fc.FigureClass]:
        classified.append(list(paths))
        return [fc.FigureClass(label="photograph", confidence=0.99, decorative_mass=0.0) for _ in paths]

    monkeypatch.setattr("doc_convert.figure_classifier.classify_figures", fake_classify)

    converter = _converter(tmp_path)
    figure_map, _kept_paths, item_refs = converter.filter_figures(
        {"#0": "figures/tiny.png", "#1": "figures/big.png"},
        [tiny, big],
        ["#0", "#1"],
    )

    assert classified == [[big]]  # the 20px image never reached the model
    assert item_refs == ["#1"]
    assert figure_map == {"#1": "figures/big.png"}


def test_filter_figures_disabled_skips_both_stages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The flag disables the whole cascade, size floor included."""

    def boom(_paths: list[Path]) -> list[fc.FigureClass]:
        raise AssertionError("classifier must not run")

    monkeypatch.setattr("doc_convert.figure_classifier.classify_figures", boom)
    tiny = _make_png(tmp_path / "tiny.png", 10, 10)

    converter = _converter(tmp_path, caption_filter=False)
    result = converter.filter_figures({"#0": "figures/tiny.png"}, [tiny], ["#0"])

    assert result == ({"#0": "figures/tiny.png"}, [tiny], ["#0"])


def test_filter_figures_empty_input(tmp_path: Path) -> None:
    converter = _converter(tmp_path)
    assert converter.filter_figures({}, [], []) == ({}, [], [])


def test_prefetch_reports_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    """--download-models uses the same load path as runtime, so success means
    the runtime path works too."""
    state = fc._Loaded(processor=object(), model=object(), device="cpu", torch=object())
    monkeypatch.setattr(fc, "_load", lambda: state)
    assert fc.prefetch() is True

    monkeypatch.setattr(fc, "_load", lambda: None)
    assert fc.prefetch() is False


class _FakeTensor:
    """Minimal stand-in for the probability tensor _forward manipulates."""

    def __init__(self, rows: int) -> None:
        self.rows = rows

    def to(self, _device: str) -> _FakeTensor:
        return self

    def float(self) -> _FakeTensor:
        return self

    def softmax(self, dim: int = -1) -> _FakeTensor:
        return self

    def max(self, dim: int = -1) -> tuple[_FakeList, _FakeList]:
        return _FakeList([0.99] * self.rows), _FakeList([0] * self.rows)

    def __getitem__(self, _key: object) -> _FakeTensor:
        """Column selection for the decorative-mass sum."""
        return self

    def sum(self, dim: int = -1) -> _FakeList:
        return _FakeList([0.99] * self.rows)


class _FakeList:
    def __init__(self, values: list[float] | list[int]) -> None:
        self.values = values

    def tolist(self) -> list[float] | list[int]:
        return self.values


class _NoGrad:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeTorch:
    @staticmethod
    def no_grad() -> _NoGrad:
        return _NoGrad()


def _fake_state(fail_on: str) -> tuple[fc._Loaded, dict[str, list[str]]]:
    """Build a classifier state whose forward pass fails on a chosen device.

    ``calls["devices"]`` records each device a forward was attempted on (tracked
    via tensor ``.to()``), ``calls["moved_to"]`` each model relocation.
    """
    calls: dict[str, list[str]] = {"devices": [], "moved_to": []}

    class TrackingTensor(_FakeTensor):
        def to(self, device: str) -> _FakeTensor:
            calls["devices"].append(device)
            return self

    class Processor:
        def __call__(self, images: list[object], return_tensors: str) -> dict[str, TrackingTensor]:
            return {"pixel_values": TrackingTensor(len(images))}

    class Model:
        config = type("C", (), {"id2label": {0: "logo"}})()

        def to(self, device: str) -> None:
            calls["moved_to"].append(device)

        def __call__(self, **_kwargs: object) -> object:
            if calls["devices"][-1] == fail_on:
                raise RuntimeError(f"{fail_on} exploded")
            return type("Out", (), {"logits": _FakeTensor(1)})()

    state = fc._Loaded(processor=Processor(), model=Model(), device="mps", torch=_FakeTorch())
    return state, calls


def test_forward_falls_back_to_cpu_on_any_mps_failure() -> None:
    """A lost retry here would silently disable the filter, so any MPS error retries."""
    state, calls = _fake_state(fail_on="mps")
    verdicts = fc._forward(state, [object()])

    assert calls["devices"] == ["mps", "cpu"]
    assert calls["moved_to"] == ["cpu"]
    assert state.device == "cpu", "the switch must stick for the rest of the run"
    assert verdicts[0].label == "logo"


def test_forward_does_not_retry_when_already_on_cpu() -> None:
    """On CPU there is nowhere to fall back to; the error must propagate to the
    batch handler, which turns it into a keep-everything UNKNOWN."""
    state, calls = _fake_state(fail_on="cpu")
    state.device = "cpu"

    with pytest.raises(RuntimeError):
        fc._forward(state, [object()])
    assert calls["devices"] == ["cpu"]
