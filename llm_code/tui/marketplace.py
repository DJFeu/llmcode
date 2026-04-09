"""Marketplace browser — scrollable list for /skill, /plugin, /mcp browsing."""
from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Static
from rich.text import Text


@dataclass(frozen=True)
class MarketplaceItem:
    """A single item in the marketplace list."""

    name: str
    description: str
    source: str  # "installed", "official", "community", "npm", "clawhub"
    category: str = ""  # grouping label: "Installed", "Official", "Community"
    installed: bool = False
    enabled: bool = True
    repo: str = ""
    extra: str = ""  # e.g. "14 skills", "v1.2.0"


class ItemRow(Widget):
    """A single selectable row in the marketplace."""

    DEFAULT_CSS = """
    ItemRow {
        height: 2;
        padding: 0 1;
    }
    ItemRow.selected {
        background: $accent 30%;
    }
    ItemRow.installed {
        color: $text;
    }
    ItemRow.available {
        color: $text-muted;
    }
    """

    def __init__(self, item: MarketplaceItem, index: int) -> None:
        super().__init__()
        self._item = item
        self._index = index
        classes = "installed" if item.installed else "available"
        self.add_class(classes)

    def render(self) -> Text:
        item = self._item
        text = Text()
        # Status indicator
        if item.installed:
            status = "enabled" if item.enabled else "disabled"
            text.append(f"[{status}] ", style="green" if item.enabled else "yellow")
        else:
            text.append("[+] ", style="dim")
        # Name
        text.append(item.name, style="bold")
        # Source tag
        text.append(f"  [{item.source}]", style="dim")
        # Extra info
        if item.extra:
            text.append(f"  {item.extra}", style="dim")
        text.append("\n")
        # Description
        text.append(f"  {item.description[:80]}", style="dim")
        return text


class MarketplaceBrowser(ModalScreen):
    """Modal screen for browsing marketplace items."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up"),
        Binding("down", "cursor_down", "Down"),
        Binding("enter", "select", "Select"),
        Binding("escape", "dismiss", "Close"),
        Binding("i", "install", "Install"),
        Binding("e", "enable_toggle", "Enable/Disable"),
        Binding("r", "remove", "Remove"),
        Binding("/", "focus_search", "Search"),
    ]

    DEFAULT_CSS = """
    MarketplaceBrowser {
        align: center middle;
    }
    #marketplace-container {
        width: 90%;
        height: 80%;
        background: $surface;
        border: round $accent;
        padding: 1;
    }
    #marketplace-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #marketplace-search {
        dock: top;
        margin: 0 1 1 1;
        height: 1;
    }
    #marketplace-hint {
        dock: bottom;
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    #marketplace-list {
        height: 1fr;
    }
    .category-header {
        height: 1;
        color: $accent;
        text-style: bold;
        padding: 0 1;
    }
    #marketplace-stats {
        height: 1;
        color: $text-muted;
        text-align: right;
        padding: 0 1;
    }
    """

    class ItemAction(Message):
        """Fired when user takes action on an item."""

        def __init__(self, action: str, item: MarketplaceItem) -> None:
            super().__init__()
            self.action = action
            self.item = item

    def __init__(self, title: str, items: list[MarketplaceItem]) -> None:
        super().__init__()
        self._title = title
        self._items = items
        self._filtered_items = list(items)
        self._cursor = 0
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="marketplace-container"):
            yield Static(self._title, id="marketplace-title")
            yield Input(placeholder="Type to filter...", id="marketplace-search")
            stats = self._stats_text()
            yield Static(stats, id="marketplace-stats")
            self._render_items()
        yield Static(
            "↑↓ Navigate · / Search · Enter/i Install · e Enable/Disable · r Remove · Esc Close",
            id="marketplace-hint",
        )

    def _stats_text(self) -> str:
        installed = sum(1 for i in self._items if i.installed)
        enabled = sum(1 for i in self._items if i.installed and i.enabled)
        available = len(self._items) - installed
        return f"{installed} installed ({enabled} enabled) · {available} available"

    def _render_items(self) -> None:
        """Mount item rows grouped by category."""
        container = self.query_one("#marketplace-container", VerticalScroll)
        # Remove existing rows and category headers
        for widget in list(container.query(ItemRow)):
            widget.remove()
        for widget in list(container.query(".category-header")):
            widget.remove()

        current_cat = ""
        idx = 0
        for item in self._filtered_items:
            cat = item.category or item.source
            if cat != current_cat:
                current_cat = cat
                container.mount(Static(f"── {cat} ──", classes="category-header"))
            container.mount(ItemRow(item, idx))
            idx += 1

    def _apply_filter(self, text: str) -> None:
        """Filter items by name or description."""
        self._filter_text = text.strip().lower()
        if not self._filter_text:
            self._filtered_items = list(self._items)
        else:
            self._filtered_items = [
                i for i in self._items
                if self._filter_text in i.name.lower()
                or self._filter_text in i.description.lower()
            ]
        self._cursor = 0
        self._render_items()
        self._update_selection()

    def on_input_changed(self, event: Input.Changed) -> None:
        """React to search input changes."""
        if event.input.id == "marketplace-search":
            self._apply_filter(event.value)

    def on_mount(self) -> None:
        self._update_selection()

    def on_key(self, event) -> None:
        """Intercept arrow keys before VerticalScroll consumes them."""
        if event.key == "up":
            self.action_cursor_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self.action_cursor_down()
            event.prevent_default()
            event.stop()

    def _update_selection(self) -> None:
        rows = list(self.query(ItemRow))
        for i, row in enumerate(rows):
            if i == self._cursor:
                row.add_class("selected")
                row.scroll_visible()
            else:
                row.remove_class("selected")

    def _selected_item(self) -> MarketplaceItem | None:
        if 0 <= self._cursor < len(self._filtered_items):
            return self._filtered_items[self._cursor]
        return None

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._update_selection()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._filtered_items) - 1:
            self._cursor += 1
            self._update_selection()

    def action_focus_search(self) -> None:
        try:
            self.query_one("#marketplace-search", Input).focus()
        except Exception:
            pass

    def action_select(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        if item.installed:
            action = "disable" if item.enabled else "enable"
        else:
            action = "install"
        self.post_message(self.ItemAction(action, item))
        self.dismiss()

    def action_install(self) -> None:
        item = self._selected_item()
        if item and not item.installed:
            self.post_message(self.ItemAction("install", item))
            self.dismiss()

    def action_enable_toggle(self) -> None:
        item = self._selected_item()
        if item and item.installed:
            action = "disable" if item.enabled else "enable"
            self.post_message(self.ItemAction(action, item))
            self.dismiss()

    def action_remove(self) -> None:
        item = self._selected_item()
        if item and item.installed:
            self.post_message(self.ItemAction("remove", item))
            self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()
