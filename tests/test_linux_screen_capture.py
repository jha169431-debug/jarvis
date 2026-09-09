import struct
from pathlib import Path

import pytest
import screen


def fake_png(width: int, height: int) -> bytes:
    """Enough of a PNG header/IHDR for screen._png_size()."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"\x00" * 16
    )


def linux_tool(name):
    return {
        "gnome-screenshot": "/usr/bin/gnome-screenshot",
        "ffmpeg": "/usr/bin/ffmpeg",
    }.get(name)


@pytest.mark.asyncio
async def test_linux_capture_uses_gnome_screenshot_and_resizes(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tool)
    monkeypatch.setattr(screen, "screen_recording_granted", lambda: None)

    calls = []

    async def fake_run(*args, timeout):
        calls.append(args)

        if args[0] == "gnome-screenshot":
            Path(args[-1]).write_bytes(fake_png(1920, 1080))
            return 0, "", ""

        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(fake_png(1280, 720))
            return 0, "", ""

        raise AssertionError(args)

    async def not_blank(_path, _workdir):
        return False

    monkeypatch.setattr(screen, "_run", fake_run)
    monkeypatch.setattr(screen, "_frame_is_blank", not_blank)

    shot = await screen.capture_screen()

    assert (shot.width, shot.height) == (1280, 720)
    assert calls[0][0] == "gnome-screenshot"
    assert calls[0][1] == "-f"

    ffmpeg = calls[1]
    assert ffmpeg[0] == "ffmpeg"
    assert "-vf" in ffmpeg
    assert "scale=1280:-2" in ffmpeg


@pytest.mark.asyncio
async def test_linux_small_capture_is_not_resized(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tool)
    monkeypatch.setattr(screen, "screen_recording_granted", lambda: None)

    calls = []

    async def fake_run(*args, timeout):
        calls.append(args)
        assert args[0] == "gnome-screenshot"
        Path(args[-1]).write_bytes(fake_png(1024, 640))
        return 0, "", ""

    async def not_blank(_path, _workdir):
        return False

    monkeypatch.setattr(screen, "_run", fake_run)
    monkeypatch.setattr(screen, "_frame_is_blank", not_blank)

    shot = await screen.capture_screen()

    assert (shot.width, shot.height) == (1024, 640)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_linux_missing_gnome_screenshot_fails_cleanly(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", lambda _name: None)
    monkeypatch.setattr(screen, "screen_recording_granted", lambda: None)

    with pytest.raises(screen.ScreenError, match="gnome-screenshot"):
        await screen.capture_screen()


@pytest.mark.asyncio
async def test_linux_individual_display_is_refused_for_now(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen, "screen_recording_granted", lambda: None)

    with pytest.raises(screen.ScreenError, match="individual Linux display"):
        await screen.capture_screen(display=2)


@pytest.mark.asyncio
async def test_linux_capture_failure_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tool)
    monkeypatch.setattr(screen, "screen_recording_granted", lambda: None)

    async def fake_run(*args, timeout):
        return 1, "", "capture failed"

    monkeypatch.setattr(screen, "_run", fake_run)

    with pytest.raises(screen.ScreenError):
        await screen.capture_screen()


@pytest.mark.asyncio
async def test_linux_blank_check_uses_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tool)

    source = tmp_path / "screen.png"
    source.write_bytes(fake_png(800, 600))

    calls = []

    async def fake_run(*args, timeout):
        calls.append(args)
        Path(args[-1]).write_bytes(b"fake bmp")
        return 0, "", ""

    monkeypatch.setattr(screen, "_run", fake_run)
    monkeypatch.setattr(
        screen,
        "_bmp_pixels",
        lambda _raw: [(0, 0, 0)] * 10,
    )
    monkeypatch.setattr(screen, "_is_blank", lambda _pixels: True)

    result = await screen._frame_is_blank(source, tmp_path)

    assert result is True
    assert calls[0][0] == "ffmpeg"
    assert "-vf" in calls[0]
    assert f"scale={screen.BLANK_SAMPLE_EDGE}:{screen.BLANK_SAMPLE_EDGE}" in calls[0]
