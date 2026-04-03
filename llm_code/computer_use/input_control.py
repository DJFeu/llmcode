"""Mouse and keyboard control via pyautogui (lazy import)."""
from __future__ import annotations

_DEFAULT_DELAY = 0.05  # 50ms between actions


def _get_pyautogui():
    """Lazy import pyautogui with clear error on missing dep."""
    try:
        import pyautogui
        return pyautogui
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui is required for input control. "
            "Install with: pip install llm-code[computer-use]"
        ) from exc


def mouse_move(x: int, y: int) -> None:
    """Move mouse cursor to (x, y)."""
    pag = _get_pyautogui()
    pag.moveTo(x, y, duration=_DEFAULT_DELAY)


def mouse_click(x: int, y: int, button: str = "left") -> None:
    """Click at (x, y) with the given button."""
    pag = _get_pyautogui()
    pag.click(x, y, button=button)


def mouse_double_click(x: int, y: int) -> None:
    """Double-click at (x, y)."""
    pag = _get_pyautogui()
    pag.doubleClick(x, y)


def mouse_drag(
    start_x: int,
    start_y: int,
    offset_x: int,
    offset_y: int,
    duration: float = 0.5,
    button: str = "left",
) -> None:
    """Drag from (start_x, start_y) by (offset_x, offset_y)."""
    pag = _get_pyautogui()
    pag.moveTo(start_x, start_y, duration=_DEFAULT_DELAY)
    pag.drag(offset_x, offset_y, duration=duration, button=button)


def keyboard_type(text: str) -> None:
    """Type the given text string character by character."""
    pag = _get_pyautogui()
    pag.typewrite(text, interval=_DEFAULT_DELAY)


def keyboard_hotkey(*keys: str) -> None:
    """Press a keyboard shortcut (e.g., keyboard_hotkey('ctrl', 'c'))."""
    pag = _get_pyautogui()
    pag.hotkey(*keys)


def scroll(clicks: int, x: int | None = None, y: int | None = None) -> None:
    """Scroll the mouse wheel. Positive = up, negative = down."""
    pag = _get_pyautogui()
    kwargs: dict = {}
    if x is not None:
        kwargs["x"] = x
    if y is not None:
        kwargs["y"] = y
    pag.scroll(clicks, **kwargs)
