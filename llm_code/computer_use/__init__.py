"""Computer use — GUI automation for llm-code."""


def is_available() -> bool:
    """Return True if pyautogui and Pillow are importable."""
    try:
        import pyautogui  # noqa: F401
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False
