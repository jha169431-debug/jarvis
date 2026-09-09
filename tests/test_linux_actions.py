import pytest
import actions


class FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode

    async def communicate(self, *_args):
        return b"", b""


@pytest.mark.asyncio
async def test_linux_terminal_uses_direct_argv(monkeypatch):
    calls = {}

    monkeypatch.setattr(actions.sys, "platform", "linux")

    def which(name):
        return {
            "gnome-terminal": "/usr/bin/gnome-terminal",
            "bash": "/bin/bash",
        }.get(name)

    async def fake_exec(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(actions.shutil, "which", which)
    monkeypatch.setattr(actions.asyncio, "create_subprocess_exec", fake_exec)

    command = 'echo "JARVIS_OK"; printf "%s" "$HOME"'
    result = await actions.open_terminal(command)

    assert result["success"] is True
    assert calls["args"] == (
        "/usr/bin/gnome-terminal",
        "--",
        "/bin/bash",
        "-lc",
        command,
    )
    assert "shell" not in calls["kwargs"]


@pytest.mark.asyncio
async def test_linux_browser_falls_back_to_xdg_open(monkeypatch):
    calls = {}

    monkeypatch.setattr(actions.sys, "platform", "linux")

    def which(name):
        if name == "xdg-open":
            return "/usr/bin/xdg-open"
        return None

    async def fake_exec(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(actions.shutil, "which", which)
    monkeypatch.setattr(actions.asyncio, "create_subprocess_exec", fake_exec)

    url = 'https://example.com/?q="$(id)";x=1'
    result = await actions.open_browser(url, "chrome")

    assert result["success"] is True
    assert calls["args"] == ("/usr/bin/xdg-open", url)
    assert "shell" not in calls["kwargs"]


@pytest.mark.asyncio
async def test_linux_editor_detaches_xdg_open(monkeypatch):
    calls = {}

    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions, "_vscode_command", lambda _path: None)
    monkeypatch.setattr(
        actions.shutil,
        "which",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)

    path = '/tmp/file;$(touch nope).txt'
    result = await actions.open_in_editor(path)

    assert result["success"] is True
    assert calls["args"] == ["/usr/bin/xdg-open", path]
    assert calls["kwargs"]["start_new_session"] is True
    assert calls["kwargs"]["stdin"] is actions.subprocess.DEVNULL
    assert calls["kwargs"]["stdout"] is actions.subprocess.DEVNULL
    assert calls["kwargs"]["stderr"] is actions.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_linux_terminal_missing_backend_fails_cleanly(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.shutil, "which", lambda _name: None)

    result = await actions.open_terminal()

    assert result["success"] is False


@pytest.mark.asyncio
async def test_linux_chrome_tab_info_fails_closed_without_osascript(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "linux")

    async def never_exec(*_args, **_kwargs):
        raise AssertionError("Linux Chrome tab lookup must not invoke osascript")

    monkeypatch.setattr(actions.asyncio, "create_subprocess_exec", never_exec)

    result = await actions.get_chrome_tab_info()

    assert result == {}
