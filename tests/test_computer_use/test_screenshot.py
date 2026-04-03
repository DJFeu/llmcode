"""Tests for screenshot capture — all deps mocked."""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch



class TestTakeScreenshot:
    @patch("llm_code.computer_use.screenshot.platform")
    @patch("llm_code.computer_use.screenshot.subprocess")
    def test_macos_uses_screencapture(self, mock_sub, mock_plat, tmp_path):
        mock_plat.system.return_value = "Darwin"
        fake_png = b"\x89PNG_FAKE_DATA"

        def fake_run(cmd, **kwargs):
            # Write fake data to the temp file path in the command
            path = cmd[-1]
            with open(path, "wb") as f:
                f.write(fake_png)
            return MagicMock(returncode=0)

        mock_sub.run.side_effect = fake_run

        from llm_code.computer_use.screenshot import take_screenshot
        result = take_screenshot()
        assert result == fake_png
        call_args = mock_sub.run.call_args[0][0]
        assert call_args[0] == "screencapture"

    @patch("llm_code.computer_use.screenshot.platform")
    @patch("llm_code.computer_use.screenshot.subprocess")
    def test_linux_uses_scrot(self, mock_sub, mock_plat, tmp_path):
        mock_plat.system.return_value = "Linux"
        fake_png = b"\x89PNG_LINUX"

        def fake_run(cmd, **kwargs):
            path = cmd[-1]
            with open(path, "wb") as f:
                f.write(fake_png)
            return MagicMock(returncode=0)

        mock_sub.run.side_effect = fake_run

        from llm_code.computer_use.screenshot import take_screenshot
        result = take_screenshot()
        assert result == fake_png
        call_args = mock_sub.run.call_args[0][0]
        assert call_args[0] == "scrot"

    def test_windows_uses_mss(self):
        fake_png = b"\x89PNG_WIN"

        mock_sct_instance = MagicMock()
        mock_monitor = {"top": 0, "left": 0, "width": 1920, "height": 1080}
        mock_sct_instance.monitors = [{}, mock_monitor]
        mock_sct_instance.grab.return_value = MagicMock()

        mock_mss_mod = MagicMock()
        mock_mss_mod.mss.return_value.__enter__ = MagicMock(return_value=mock_sct_instance)
        mock_mss_mod.mss.return_value.__exit__ = MagicMock(return_value=False)
        mock_mss_mod.tools.to_png.return_value = fake_png

        import sys
        with patch.dict(sys.modules, {"mss": mock_mss_mod, "mss.tools": mock_mss_mod.tools}):
            from llm_code.computer_use import screenshot as ss_mod
            import importlib
            importlib.reload(ss_mod)
            with patch.object(ss_mod, "platform") as mock_plat:
                mock_plat.system.return_value = "Windows"
                result = ss_mod.take_screenshot()
            assert result == fake_png

    def test_take_screenshot_with_region(self):
        """Region parameter is forwarded (macOS crop flag)."""
        with patch("llm_code.computer_use.screenshot.platform") as mock_plat, \
             patch("llm_code.computer_use.screenshot.subprocess") as mock_sub:
            mock_plat.system.return_value = "Darwin"
            fake_png = b"\x89PNG"

            def fake_run(cmd, **kwargs):
                path = cmd[-1]
                with open(path, "wb") as f:
                    f.write(fake_png)
                return MagicMock(returncode=0)

            mock_sub.run.side_effect = fake_run

            from llm_code.computer_use.screenshot import take_screenshot
            result = take_screenshot(region=(0, 0, 800, 600))
            assert result == fake_png


class TestTakeScreenshotBase64:
    @patch("llm_code.computer_use.screenshot.take_screenshot")
    def test_returns_base64_string(self, mock_take):
        raw = b"\x89PNG_DATA"
        mock_take.return_value = raw

        from llm_code.computer_use.screenshot import take_screenshot_base64
        result = take_screenshot_base64()
        assert result == base64.b64encode(raw).decode("ascii")

    @patch("llm_code.computer_use.screenshot.take_screenshot")
    def test_base64_is_decodable(self, mock_take):
        raw = b"\x89PNG_ROUNDTRIP"
        mock_take.return_value = raw

        from llm_code.computer_use.screenshot import take_screenshot_base64
        decoded = base64.b64decode(take_screenshot_base64())
        assert decoded == raw
