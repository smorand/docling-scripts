"""Tests for pure markdown-building helpers."""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc.document import PictureItem

from doc_convert.markdown import (
    FloatingArtifacts,
    FloatingContext,
    _extract_label,
    _find_mention,
    _format_description_block,
    _heading_for,
    _render_item_lines,
    _shorten_sentence,
    build_document_markdown,
    build_images_catalog,
    build_pptx_slides_markdown,
    get_pdf_metadata,
)


class _FakeProv:
    def __init__(self, page_no: int) -> None:
        self.page_no = page_no


class _FakeTextItem:
    """Duck-typed stand-in for a docling text item (not a Table/PictureItem)."""

    def __init__(self, text: str, page_no: int, *, label: str = "paragraph", ref: str = "") -> None:
        self.text = text
        self.label = label
        self.prov = [_FakeProv(page_no)]
        self.self_ref = ref


class _FakeDoc:
    def __init__(self, items: list) -> None:
        self._items = items

    def iterate_items(self):
        return [(item, 0) for item in self._items]


def test_extract_label() -> None:
    assert _extract_label("Figure 3: Architecture diagram") == "Figure 3"
    assert _extract_label("fig. 2 overview") == "Figure 2"
    assert _extract_label("Table 1") == "Table 1"
    assert _extract_label("no marker here") == ""
    assert _extract_label("") == ""


def test_shorten_sentence() -> None:
    assert _shorten_sentence("  a   b\tc ") == "a b c"
    long = "word " * 100
    out = _shorten_sentence(long, max_chars=20)
    assert len(out) <= 20
    assert out.endswith("…")


def test_find_mention() -> None:
    body = ["Intro text.", "As shown in Figure 2, the trend rises.", "Other."]
    assert "Figure 2" in _find_mention(body, "2", "Figure")
    assert _find_mention(body, "9", "Figure") == ""


def test_heading_for() -> None:
    assert _heading_for("Figure 1", "Figure", "Figure 1: Layout") == "#### Figure 1: Layout"
    assert _heading_for("Figure 1", "Figure", "") == "#### Figure 1"
    assert _heading_for("", "Figure", "Caption only") == "#### Figure: Caption only"
    assert _heading_for("", "Figure", "") == "#### Figure"


def test_format_description_block_empty() -> None:
    assert _format_description_block("", "") == ""


def test_format_description_block_description_only() -> None:
    block = _format_description_block("A chart\nshowing growth", "")
    assert block == "> A chart\n> showing growth"


def test_format_description_block_with_mention() -> None:
    block = _format_description_block("Desc", "Figure 1 shows X")
    assert "> Desc" in block
    assert "Cited in document" in block


def test_get_pdf_metadata_invalid_file(tmp_path: Path) -> None:
    fake = tmp_path / "not-really.pdf"
    fake.write_text("not a pdf")
    meta = get_pdf_metadata(str(fake))
    # Gracefully degrades: at least the filename is recorded.
    assert meta["File"] == "not-really.pdf"


# ── build_pptx_slides_markdown (3-section-per-slide PPTX output) ──────────────


def test_build_pptx_slides_markdown_basic_structure() -> None:
    doc = _FakeDoc(
        [
            _FakeTextItem("Hello slide 1", page_no=1),
            _FakeTextItem("Hello slide 2", page_no=2),
        ]
    )
    md = build_pptx_slides_markdown(
        doc,
        FloatingArtifacts(),
        {},
        visual_by_slide={1: "Visual take on slide 1", 2: "Visual take on slide 2"},
        notes_by_slide={1: "Speaker note for slide 1"},
        slide_count=2,
        title="My Deck",
    )
    assert md.startswith("# My Deck")
    assert "## Slide 1" in md
    assert "## Slide 2" in md
    assert "### Extracted Content (text + figures)" in md
    assert "### Visual Interpretation (full slide screenshot)" in md
    assert "### Speaker Notes" in md
    assert "Hello slide 1" in md
    assert "Hello slide 2" in md
    assert "Visual take on slide 1" in md
    assert "Visual take on slide 2" in md
    assert "Speaker note for slide 1" in md
    # Slide 1 has notes, slide 2 doesn't: fallback placeholder shows once.
    assert md.count("_No speaker notes._") == 1
    # Slide ordering: slide 1's section must appear before slide 2's.
    assert md.index("## Slide 1") < md.index("## Slide 2")


def test_build_pptx_slides_markdown_empty_slide_fallback() -> None:
    doc = _FakeDoc([])
    md = build_pptx_slides_markdown(
        doc,
        FloatingArtifacts(),
        {},
        visual_by_slide={1: ""},
        notes_by_slide={},
        slide_count=1,
    )
    assert "_No extractable text content on this slide._" in md
    assert "_No visual interpretation available._" in md
    assert "_No speaker notes._" in md


def test_build_pptx_slides_markdown_slide_count_extends_beyond_items() -> None:
    # slide_count can exceed the highest page_no seen in items (e.g. a trailing
    # blank slide with no extractable text but a screenshot was still rendered).
    doc = _FakeDoc([_FakeTextItem("Only slide 1 has text", page_no=1)])
    md = build_pptx_slides_markdown(
        doc,
        FloatingArtifacts(),
        {},
        visual_by_slide={1: "desc1", 2: "desc2", 3: "desc3"},
        notes_by_slide={},
        slide_count=3,
    )
    assert "## Slide 1" in md
    assert "## Slide 2" in md
    assert "## Slide 3" in md
    assert "desc3" in md


def test_build_pptx_slides_markdown_marks_hidden_slides() -> None:
    doc = _FakeDoc([_FakeTextItem("Hello slide 1", page_no=1), _FakeTextItem("Hello slide 2", page_no=2)])
    md = build_pptx_slides_markdown(
        doc,
        FloatingArtifacts(),
        {},
        visual_by_slide={1: "v1", 2: "v2"},
        notes_by_slide={},
        slide_count=2,
        hidden_slides={2},
    )
    assert "## Slide 1\n" in md
    assert "## Slide 2 *(hidden in source presentation)*" in md


def test_build_pptx_slides_markdown_section_header_offset() -> None:
    # Section headers inside a slide must nest under '## Slide N' / '### Extracted
    # Content', not collide with them (heading_offset=3 for the PPTX grouping).
    doc = _FakeDoc([_FakeTextItem("Intro", page_no=1, label="section_header")])
    md = build_pptx_slides_markdown(doc, FloatingArtifacts(), {}, visual_by_slide={}, notes_by_slide={}, slide_count=1)
    # level defaults to 1 -> "#" * min(1+3, 6) == "####"
    assert "#### Intro" in md


def test_picture_item_without_figure_path_disappears() -> None:
    """A figure dropped by the caption filter (no entry in figure_paths) must
    produce no output at all: no heading, no description, no dangling link."""
    item = PictureItem(self_ref="#/pictures/0")
    lines = _render_item_lines(item, doc=None, ref="#/pictures/0", artifacts=FloatingArtifacts(), contexts={})
    assert lines == []


def test_picture_item_with_figure_path_renders_normally() -> None:
    """Sanity check: a figure that does have a figure_paths entry still renders as before."""
    item = PictureItem(self_ref="#/pictures/0")
    artifacts = FloatingArtifacts(figure_paths={"#/pictures/0": "figures/figure_0.png"})
    lines = _render_item_lines(item, doc=None, ref="#/pictures/0", artifacts=artifacts, contexts={})
    joined = "\n".join(lines)
    assert "#### Figure" in joined
    assert "figures/figure_0.png" in joined


def test_slide_with_only_filtered_figures_gets_placeholder() -> None:
    """A slide whose every item was dropped by the caption filter must show the
    placeholder, not a silently empty 'Extracted Content' section (ambiguous for
    a downstream LLM: blank slide vs failed extraction vs filtered)."""
    pic = PictureItem(self_ref="#/pictures/0")
    pic.prov = [_FakeProv(1)]
    doc = _FakeDoc([pic])
    md = build_pptx_slides_markdown(
        doc,
        FloatingArtifacts(),  # no figure_paths entry -> the figure was filtered
        {},
        visual_by_slide={1: "a chart of revenue"},
        notes_by_slide={},
        slide_count=1,
    )
    assert "_No extractable text content on this slide._" in md
    # The other two sections must still carry their content.
    assert "a chart of revenue" in md
    assert "_No speaker notes._" in md


# ---------------------------------------------------------------------------
# Repeated figures: describe once, point at it afterwards
# ---------------------------------------------------------------------------


class _FakePicture(PictureItem):
    """A PictureItem placed on a given page/slide."""

    def __init__(self, ref: str, page_no: int) -> None:
        super().__init__(self_ref=ref)
        self.prov = [_FakeProv(page_no)]


def _artifacts_for(refs_to_path: dict[str, str], description: str) -> FloatingArtifacts:
    return FloatingArtifacts(
        figure_paths=dict(refs_to_path),
        figure_descriptions=dict.fromkeys(refs_to_path, description),
    )


def test_repeated_figure_is_described_once_in_document_markdown() -> None:
    """The same image on three pages must carry its description once and point at
    it twice: a template logo on 36 slides was 24% of a real document.md."""
    desc = "A very long description of the same template banner."
    items = [_FakePicture(f"#/pictures/{i}", page_no=i + 1) for i in range(3)]
    artifacts = _artifacts_for({it.self_ref: "figures/figure_2.png" for it in items}, desc)

    md = build_document_markdown(_FakeDoc(items), artifacts, {}, paginated=True)

    assert md.count(desc) == 1, "description must appear exactly once"
    assert md.count("*Image: [`figures/figure_2.png`]") == 3, "every placement keeps its image link"
    assert md.count("Same image as under Page 1; description given there.") == 2
    assert md.count("#### Figure") == 3, "every placement keeps its heading"


def test_distinct_figures_each_keep_their_description() -> None:
    """Dedup must key on the image file, not on the fact that a figure repeats."""
    items = [_FakePicture("#/pictures/0", 1), _FakePicture("#/pictures/1", 2)]
    artifacts = FloatingArtifacts(
        figure_paths={"#/pictures/0": "figures/a.png", "#/pictures/1": "figures/b.png"},
        figure_descriptions={"#/pictures/0": "describes A", "#/pictures/1": "describes B"},
    )

    md = build_document_markdown(_FakeDoc(items), artifacts, {}, paginated=True)

    assert "describes A" in md
    assert "describes B" in md
    assert "Same image as" not in md


def test_repeat_keeps_its_own_citing_sentence() -> None:
    """The description is per image, but the sentence citing it differs per
    placement and must survive on every occurrence."""
    items = [_FakePicture("#/pictures/0", 1), _FakePicture("#/pictures/1", 2)]
    artifacts = _artifacts_for({it.self_ref: "figures/shared.png" for it in items}, "shared description")
    contexts = {
        "#/pictures/0": FloatingContext(mention="first mention here"),
        "#/pictures/1": FloatingContext(mention="a different mention"),
    }

    md = build_document_markdown(_FakeDoc(items), artifacts, contexts, paginated=True)

    assert md.count("shared description") == 1
    assert "first mention here" in md
    assert "a different mention" in md


def test_pptx_repeat_points_at_the_slide() -> None:
    """PPTX locations must read as slides, not pages."""
    items = [_FakePicture("#/pictures/0", 4), _FakePicture("#/pictures/1", 9)]
    artifacts = _artifacts_for({it.self_ref: "figures/banner.png" for it in items}, "the banner description")

    md = build_pptx_slides_markdown(
        _FakeDoc(items),
        artifacts,
        {},
        visual_by_slide={},
        notes_by_slide={},
        slide_count=9,
    )

    assert md.count("the banner description") == 1
    assert "Same image as under Slide 4; description given there." in md


def test_images_catalog_lists_each_file_once_with_every_page() -> None:
    """A catalog that lists the same picture 36 times is not a catalog: 55% of a
    real 313 KB images.md was duplicate entries."""
    items = [_FakePicture("#/pictures/0", 2), _FakePicture("#/pictures/1", 7), _FakePicture("#/pictures/2", 11)]
    artifacts = _artifacts_for({it.self_ref: "figures/logo.png" for it in items}, "the logo description")

    catalog = build_images_catalog(_FakeDoc(items), artifacts, {})

    assert catalog.count("- **Path:** `figures/logo.png`") == 1
    assert catalog.count("the logo description") == 1
    assert "- **Pages:** 2, 7, 11" in catalog


def test_images_catalog_merges_context_from_later_placements() -> None:
    """A later placement can carry a caption the first one lacked."""
    items = [_FakePicture("#/pictures/0", 1), _FakePicture("#/pictures/1", 2)]
    artifacts = _artifacts_for({it.self_ref: "figures/x.png" for it in items}, "desc")
    contexts = {
        "#/pictures/0": FloatingContext(),
        "#/pictures/1": FloatingContext(caption="Figure 4: the real caption", mention="cited late"),
    }

    catalog = build_images_catalog(_FakeDoc(items), artifacts, contexts)

    assert "- **Caption:** Figure 4: the real caption" in catalog
    assert "- **Mention:** cited late" in catalog
    assert catalog.count("- **Path:** `figures/x.png`") == 1
