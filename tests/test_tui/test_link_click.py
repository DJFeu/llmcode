"""Tests for clickable link region tracking in AssistantText."""
from __future__ import annotations

from llm_code.tui.chat_view import (
    AssistantText,
    LinkRegion,
    _styled_text_with_regions,
)


def test_returns_text_and_regions_tuple():
    text, regions = _styled_text_with_regions("see [docs](https://example.com)")
    assert text.plain.startswith("see docs")
    assert len(regions) == 1
    region = regions[0]
    assert region.url == "https://example.com"
    assert region.row == 0
    assert region.col_start == 4
    assert region.col_end == 8  # "docs"


def test_cjk_label_width_uses_cells_not_chars():
    _, regions = _styled_text_with_regions("[新聞](https://example.com)")
    assert len(regions) == 1
    region = regions[0]
    # 新 and 聞 are each width 2 → 4 cells total, not 2
    assert region.col_end - region.col_start == 4


def test_multi_line_row_indexes():
    src = "first [a](https://a.example)\nsecond [b](https://b.example)"
    _, regions = _styled_text_with_regions(src)
    assert len(regions) == 2
    by_url = {r.url: r for r in regions}
    assert by_url["https://a.example"].row == 0
    assert by_url["https://b.example"].row == 1


def test_bare_url_recorded_as_region():
    _, regions = _styled_text_with_regions("visit https://foo.example now")
    assert len(regions) == 1
    region = regions[0]
    assert region.url == "https://foo.example"
    # Region width should match the URL string length in cells
    assert region.col_end - region.col_start == len("https://foo.example")


def test_two_links_same_line_non_overlapping():
    _, regions = _styled_text_with_regions("[a](https://x) [b](https://y)")
    assert len(regions) == 2
    a, b = regions
    assert a.row == 0 and b.row == 0
    assert a.col_end <= b.col_start
    assert a.url == "https://x"
    assert b.url == "https://y"


def test_link_region_contains():
    region = LinkRegion(row=2, col_start=5, col_end=10, url="https://x")
    assert region.contains(5, 2)
    assert region.contains(9, 2)
    assert not region.contains(10, 2)  # exclusive end
    assert not region.contains(5, 1)
    assert not region.contains(4, 2)


def test_assistant_text_find_link_after_render():
    widget = AssistantText("see [docs](https://example.com)")
    # Trigger render to populate _link_regions
    widget.render()
    found = widget._find_link(4, 0)
    assert found is not None
    assert found.url == "https://example.com"
    assert widget._find_link(0, 0) is None


def test_assistant_text_append_text_rebuilds_regions():
    widget = AssistantText("hello ")
    widget.render()
    assert widget._link_regions == ()
    widget._text += "[docs](https://example.com)"
    widget.render()
    assert len(widget._link_regions) == 1
    assert widget._link_regions[0].url == "https://example.com"
