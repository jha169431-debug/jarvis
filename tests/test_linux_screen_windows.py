import pytest
import screen


def linux_tools(name):
    return {
        "wmctrl": "/usr/bin/wmctrl",
        "xprop": "/usr/bin/xprop",
    }.get(name)


@pytest.mark.asyncio
async def test_linux_windows_parse_titles_classes_and_frontmost(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tools)

    async def fake_run(*args, timeout):
        if args[0] == "xprop":
            return 0, (
                "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0460000b\n"
            ), ""

        assert args == ("wmctrl", "-lx")
        return 0, (
            "garbage line\n"
            "0x0440000a  0 firefox.firefox host "
            "GitHub Repository Summary — Mozilla Firefox\n"
            "0x0460000b  0 gnome-terminal-server.Gnome-terminal host "
            "nitesh@host: /repo\n"
        ), ""

    monkeypatch.setattr(screen, "_run", fake_run)

    windows = await screen.list_windows()

    assert [(w.app, w.title, w.frontmost) for w in windows] == [
        (
            "firefox",
            "GitHub Repository Summary — Mozilla Firefox",
            False,
        ),
        (
            "Gnome-terminal",
            "nitesh@host: /repo",
            True,
        ),
    ]


@pytest.mark.asyncio
async def test_linux_window_list_is_bounded(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tools)

    async def fake_run(*args, timeout):
        if args[0] == "xprop":
            return 0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1\n", ""

        rows = "".join(
            f"0x{i + 1000:x}  0 app{i}.App{i} host Window {i}\n"
            for i in range(screen.MAX_WINDOWS + 20)
        )
        return 0, rows, ""

    monkeypatch.setattr(screen, "_run", fake_run)

    windows = await screen.list_windows()
    assert len(windows) == screen.MAX_WINDOWS


@pytest.mark.asyncio
async def test_linux_missing_window_tools_fails_cleanly(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", lambda _name: None)

    with pytest.raises(screen.ScreenError):
        await screen.list_windows()


@pytest.mark.asyncio
async def test_linux_xprop_failure_is_not_reported_as_empty_desk(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tools)

    async def fake_run(*args, timeout):
        assert args[0] == "xprop"
        return 1, "", "cannot read root window"

    monkeypatch.setattr(screen, "_run", fake_run)

    with pytest.raises(screen.ScreenError):
        await screen.list_windows()


@pytest.mark.asyncio
async def test_linux_wmctrl_failure_is_not_reported_as_empty_desk(monkeypatch):
    monkeypatch.setattr(screen.sys, "platform", "linux")
    monkeypatch.setattr(screen.shutil, "which", linux_tools)

    async def fake_run(*args, timeout):
        if args[0] == "xprop":
            return 0, "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x123\n", ""
        return 1, "", "wmctrl failed"

    monkeypatch.setattr(screen, "_run", fake_run)

    with pytest.raises(screen.ScreenError):
        await screen.list_windows()
