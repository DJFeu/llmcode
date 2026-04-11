"""E2E: `/image <path>` attaches an image to the pending queue."""
from __future__ import annotations

import base64


# Smallest valid PNG: 1x1 transparent pixel.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)


async def test_image_command_accepts_png_path(pilot_app, tmp_path):
    """`/image /path/to/foo.png` should load the file, append an
    ImageBlock to _pending_images, and bump the InputBar marker
    count so the prompt renders a `[1 image]` tag."""
    from llm_code.api.types import ImageBlock
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)

    img_path = tmp_path / "tiny.png"
    img_path.write_bytes(_TINY_PNG)

    assert len(app._pending_images) == 0
    assert bar.pending_image_count == 0

    app._cmd_dispatcher.dispatch("image", str(img_path))
    await pilot.pause()

    assert len(app._pending_images) == 1
    assert isinstance(app._pending_images[0], ImageBlock)
    assert app._pending_images[0].media_type == "image/png"
    assert bar.pending_image_count == 1


async def test_image_command_no_arg_prints_usage(pilot_app):
    """`/image` without a path should print a usage message and not
    touch the pending queue."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("image", "")
    await pilot.pause()

    from tests.test_e2e_tui.test_boot_banner import _rendered_text
    rendered = _rendered_text(chat)
    assert "Usage: /image" in rendered
    assert len(app._pending_images) == 0


async def test_image_command_missing_file_surfaces_error(pilot_app, tmp_path):
    """A non-existent path should print an error and NOT crash."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    bogus = tmp_path / "definitely-not-a-real-file.png"
    app._cmd_dispatcher.dispatch("image", str(bogus))
    await pilot.pause()

    from tests.test_e2e_tui.test_boot_banner import _rendered_text
    rendered = _rendered_text(chat)
    assert "Image not found" in rendered
    assert len(app._pending_images) == 0


async def test_multiple_images_accumulate(pilot_app, tmp_path):
    """Each `/image <path>` should append, not replace."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)

    # Make two files and attach both.
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(_TINY_PNG)
        app._cmd_dispatcher.dispatch("image", str(tmp_path / name))
        await pilot.pause()

    assert len(app._pending_images) == 2
    assert bar.pending_image_count == 2
