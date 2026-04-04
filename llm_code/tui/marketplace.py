"""Marketplace browser — scrollable list for /skill, /plugin, /mcp browsing."""
from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static
from rich.text import Text


@dataclass(frozen=True)
class MarketplaceItem:
    """A single item in the marketplace list."""

    name: str
    description: str
    source: str  # "installed", "official", "community", "npm", "clawhub"
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
    #marketplace-hint {
        dock: bottom;
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    #marketplace-list {
        height: 1fr;
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
        self._cursor = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="marketplace-container"):
            yield Static(self._title, id="marketplace-title")
            for i, item in enumerate(self._items):
                yield ItemRow(item, i)
        yield Static(
            "↑↓ Navigate · Enter/i Install · e Enable/Disable · r Remove · Esc Close",
            id="marketplace-hint",
        )

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
        if 0 <= self._cursor < len(self._items):
            return self._items[self._cursor]
        return None

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._update_selection()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._items) - 1:
            self._cursor += 1
            self._update_selection()

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
