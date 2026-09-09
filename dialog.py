"""Press one key in the terminal a Claude Code session is running in.

This is the most dangerous module in the project: it sends a synthetic
keystroke to a window on the user's machine. Aimed wrong, it types into
whatever the user is actually working in. Every decision here exists to make
that impossible, so read the three of them before changing anything.

**1. The target is found by tty, never by focus.** A session's pid maps to a
tty (`ps -o tty=`), and Terminal.app publishes the tty of every tab over
AppleScript. That gives an exact pid -> tty -> tab identity with no guessing.
If no Terminal tab owns that tty we return `not_found` and press NOTHING.
There is deliberately no "send it to the front window" fallback: on this
developer's machine the sessions live in Orcha.app, an Electron host whose
tabs AppleScript cannot address, and its ttys do not intersect Terminal's at
all — so a focus fallback would type into an unrelated window every single
time. `not_found` is the common case, and it is the correct one.

**2. The vocabulary is closed.** Return, Escape and a single digit 1-9. That
is the whole set, and it is a safety boundary rather than a convenience:
anything else — free text, an empty string, a multi-character string, a shell
fragment — is refused before any AppleScript is composed, let alone run. This
module never types text.

**3. Nothing here raises.** Every path returns an outcome string, so the
caller can always say something useful and always has something to audit.

Terminal.app only. iTerm2 exposes `tty` per session in much the same shape and
would slot in at `find_terminal_tab`, but it is not installed here, so it is
not claimed and not built. TIOCSTI — the host-independent way to do this — is
refused by macOS 26.2 with PermissionError even on a pty the process owns;
do not go looking for it again.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.dialog")

# --- outcomes ---------------------------------------------------------------
SENT = "sent"
NO_TTY = "no_tty"           # the pid is dead, or has no controlling terminal
NOT_FOUND = "not_found"     # no Terminal.app tab owns that tty (another host)
NOT_PERMITTED = "not_permitted"   # macOS Accessibility/automation refused us
FAILED = "failed"           # osascript died, timed out, or said something odd
BAD_KEY = "bad_key"         # defensive: a key outside the closed vocabulary

# How long an osascript may run before we stop waiting and kill it. A hung
# osascript (a modal sheet on Terminal, a permission dialog nobody answers)
# must not wedge the caller, which is a voice turn with a person waiting on it.
LOOKUP_TIMEOUT = 10.0
SEND_TIMEOUT = 20.0

# How long `ps` and `pgrep` may take. Both are local, answer in milliseconds,
# and are only ever consulted to decide whether to do nothing — so a long
# ceiling buys nothing and costs a great deal. server.py's
# `_tty_for_session_or_explain` is still SYNCHRONOUS and calls `tty_for_pid`
# once per pid on the voice loop; at the old 5s ceiling a five-process
# session was up to twenty-five seconds of frozen microphone. This bounds
# that until that caller can be made async (see `tty_for_pid_async`).
_PS_TIMEOUT = 1.0

# The closed vocabulary. `key code` numbers rather than `keystroke return`,
# because a key code is unambiguous and cannot be reinterpreted as text.
_RETURN_KEY_CODE = 36
_ESCAPE_KEY_CODE = 53

_ALIASES = {
    "enter": "return",
    "return": "return",
    "yes": "return",       # "yes" answers a permission prompt with Return
    "y": "return",
    "escape": "escape",
    "esc": "escape",
    "cancel": "escape",
    "no": "escape",
    "n": "escape",
}

# Substrings macOS uses when it is Accessibility/automation refusing us, not
# a bug in the script. Each is distinctive enough that it cannot match the
# text of an ordinary AppleScript error.
_PERMISSION_MARKERS = (
    "-1743",                              # not authorized to send Apple events
    "-25211",                             # osascript is not allowed assistive access
    "not allowed assistive access",
    "not authorized to send apple events",
    "is not allowed to send keystrokes",
    "assistive access",
)


@dataclass(frozen=True)
class TerminalTab:
    """Enough to re-select one Terminal.app tab, plus the tty that identified
    it — kept so the send script can re-check the identity at press time."""
    window_id: int
    tab_index: int
    tty: str


@dataclass(frozen=True)
class TmuxPane:
    """Exact Linux tmux target derived from the target process itself."""
    socket_path: str
    server_pid: int
    pane_id: str
    tty: str
    tmux_env: str


def normalize_key(key) -> str | None:
    """The closed vocabulary, or None. None means REFUSE — never interpret.

    Returns "return", "escape", or a single digit "1".."9". Anything else,
    including free text that merely starts with an accepted word, is None.
    """
    if not isinstance(key, str):
        return None
    k = key.strip().lower()
    if k in _ALIASES:
        return _ALIASES[k]
    if len(k) == 1 and k in "123456789":
        return k
    return None


def spoken_key(normalized: str) -> str:
    """How the read-back names the key. Must match what actually gets sent."""
    return {"return": "Return", "escape": "Escape"}.get(normalized, normalized)


def normalize_tty(tty: str | None) -> str | None:
    """`ttys006` and `/dev/ttys006` are the same device; Terminal reports the
    long form and `ps` the short one. Everything downstream compares the long
    form so the match can be exact rather than a suffix test — `ttys1` must
    never match `ttys11`."""
    if not tty:
        return None
    t = tty.strip()
    if not t or t == "??":
        return None
    if not t.startswith("/dev/"):
        t = "/dev/" + t
    # macOS Terminal uses /dev/ttysNNN. Linux pseudoterminals use
    # /dev/pts/N. Accept only those exact device-path shapes.
    if re.fullmatch(r"/dev/tty[a-zA-Z0-9]+", t):
        return t
    if re.fullmatch(r"/dev/pts/[0-9]+", t):
        return t
    return None


def tty_for_pid(pid) -> str | None:
    """`/dev/ttysNNN` for a live pid with a controlling terminal, else None.

    None covers all three of: a dead pid, a pid `ps` reports as `??` (a
    session started without a terminal — the `sdk-cli` entrypoint on this
    machine is one), and anything unparseable.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        import subprocess
        out = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)],
                             capture_output=True, text=True,
                             timeout=_PS_TIMEOUT)
    except Exception as e:
        log.warning(f"tty lookup for pid {pid} failed: {e}")
        return None
    if out.returncode != 0:
        return None                      # ps exits non-zero for a dead pid
    return normalize_tty(out.stdout.strip())


async def tty_for_pid_async(pid) -> str | None:
    """`tty_for_pid` off the event loop.

    Identical answer; the `ps` call just happens on a worker thread. Anything
    async must use this one — a blocking subprocess on the voice loop is a
    frozen microphone, and `ps` is exactly where that used to happen.

    The synchronous `tty_for_pid` stays as the function tests patch (this
    one delegates to it, so a patch covers both) and for any caller that is
    genuinely synchronous. Nothing on the voice path calls it directly any
    more.
    """
    return await asyncio.to_thread(tty_for_pid, pid)


def _terminal_is_running() -> bool:
    """True only if Terminal.app already has a process.

    Checked with pgrep and NOT with AppleScript, because `tell application
    "Terminal"` LAUNCHES it. A read-only "is my session in a Terminal tab?"
    lookup must never open an application on the user's screen. Anything
    unexpected answers False, which costs a `not_found` and opens nothing.
    """
    if shutil.which("pgrep") is None:
        return False
    try:
        import subprocess
        return subprocess.run(["pgrep", "-x", "Terminal"],
                              capture_output=True,
                              timeout=_PS_TIMEOUT).returncode == 0
    except Exception:
        return False


async def _osascript(script: str, timeout: float) -> tuple[int, str, str]:
    """Run one AppleScript. Returns (returncode, stdout, stderr).

    A timeout kills the child and comes back as returncode -1 so the caller
    sees a failure rather than hanging on a script that will never return.
    """
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        log.warning(f"osascript timed out after {timeout}s")
        return -1, "", "timeout"
    return (proc.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"))


def _is_permission_error(stderr: str) -> bool:
    low = stderr.lower()
    return any(m in low for m in _PERMISSION_MARKERS)


_TMUX_PANE_RE = re.compile(r"%[0-9]+")
_TMUX_TIMEOUT = 2.0


def _proc_tmux_env(pid) -> dict[str, str] | None:
    """Read only the tmux identity variables from a Linux process.

    The target process is the authority for which tmux server and pane it was
    born in. Nothing from JARVIS's own ambient TMUX environment is used.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None

    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except (OSError, ValueError):
        return None

    out: dict[str, str] = {}
    for item in data.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, _, value = item.partition(b"=")
        if key == b"TMUX":
            out["TMUX"] = os.fsdecode(value)
        elif key == b"TMUX_PANE":
            try:
                out["TMUX_PANE"] = value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return out


def _tmux_target_from_env(env: dict[str, str] | None,
                          tty: str | None) -> TmuxPane | None:
    """Parse a strict tmux identity and bind it to exactly one tty."""
    if not env:
        return None

    raw_tmux = env.get("TMUX", "")
    pane_id = env.get("TMUX_PANE", "")
    want_tty = normalize_tty(tty)

    if want_tty is None or _TMUX_PANE_RE.fullmatch(pane_id) is None:
        return None

    # TMUX is socket-path,server-pid,session-index. rsplit keeps commas in a
    # socket path from changing which two fields are treated as metadata.
    parts = raw_tmux.rsplit(",", 2)
    if len(parts) != 3:
        return None

    socket_path, server_pid_text, session_index = parts
    if not socket_path or not Path(socket_path).is_absolute():
        return None
    if not server_pid_text.isdecimal() or not session_index.isdecimal():
        return None

    server_pid = int(server_pid_text)
    if server_pid <= 0:
        return None

    return TmuxPane(
        socket_path=socket_path,
        server_pid=server_pid,
        pane_id=pane_id,
        tty=want_tty,
        tmux_env=raw_tmux,
    )


async def _run_tmux(socket_path: str, *args: str,
                    timeout: float = _TMUX_TIMEOUT) -> tuple[int, str, str]:
    """Run tmux against one explicit server socket. Never invokes a shell."""
    tmux = shutil.which("tmux")
    if tmux is None:
        return 127, "", "tmux not installed"

    try:
        proc = await asyncio.create_subprocess_exec(
            tmux, "-S", socket_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)

    return (
        proc.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def _tmux_state_matches(target: TmuxPane) -> bool:
    """Revalidate server, pane, tty and input state in one tmux query."""
    code, stdout, stderr = await _run_tmux(
        target.socket_path,
        "display-message",
        "-p",
        "-t",
        target.pane_id,
        "#{pid} #{pane_id} #{pane_tty} #{pane_in_mode} #{pane_input_off}",
    )
    if code != 0:
        log.warning(
            f"tmux pane lookup failed ({code}): {stderr.strip()}")
        return False

    parts = stdout.strip().split()
    if len(parts) != 5:
        return False

    server_pid, pane_id, pane_tty, pane_in_mode, pane_input_off = parts
    return (
        server_pid == str(target.server_pid)
        and pane_id == target.pane_id
        and normalize_tty(pane_tty) == target.tty
        and pane_in_mode == "0"
        and pane_input_off == "0"
    )


async def _answer_linux_tmux(pid: int, tty: str,
                             normalized: str) -> str:
    """Send one key only to a positively identified tmux pane.

    Ordinary GNOME Terminal deliberately returns NOT_FOUND: its D-Bus screen
    object identifies the tab but does not expose a safe input method.
    """
    if shutil.which("tmux") is None:
        return NOT_FOUND

    env = await asyncio.to_thread(_proc_tmux_env, pid)
    target = _tmux_target_from_env(env, tty)
    if target is None:
        return NOT_FOUND

    # First identity check.
    if not await _tmux_state_matches(target):
        return NOT_FOUND

    # Re-read the process itself immediately before the send. Its tty, TMUX
    # server or pane may have changed since lookup.
    current_tty = await tty_for_pid_async(pid)
    if current_tty != target.tty:
        return NOT_FOUND

    current_env = await asyncio.to_thread(_proc_tmux_env, pid)
    current_target = _tmux_target_from_env(current_env, current_tty)
    if current_target != target:
        return NOT_FOUND

    # Second tmux-side identity check immediately before send-keys.
    if not await _tmux_state_matches(target):
        return NOT_FOUND

    if normalized == "return":
        args = ("send-keys", "-t", target.pane_id, "Enter")
    elif normalized == "escape":
        args = ("send-keys", "-t", target.pane_id, "Escape")
    else:
        # Digits are literal characters, never tmux key names.
        args = ("send-keys", "-l", "-t", target.pane_id, normalized)

    code, _, stderr = await _run_tmux(target.socket_path, *args)
    if code == 0:
        return SENT

    log.warning(f"tmux send-keys failed ({code}): {stderr.strip()}")
    return FAILED


_ENUMERATE_SCRIPT = '''
tell application "Terminal"
    set out to ""
    repeat with w in windows
        set wid to (id of w) as text
        set n to (count of tabs of w)
        repeat with i from 1 to n
            set tt to ""
            try
                set tt to (tty of tab i of w) as text
            end try
            set out to out & wid & ":" & (i as text) & ":" & tt & linefeed
        end repeat
    end repeat
    return out
end tell
'''


async def find_terminal_tab(tty: str | None) -> TerminalTab | None:
    """The Terminal.app tab whose tty is EXACTLY `tty`, or None.

    None is a normal, expected answer — it is what a session hosted by any
    application other than Terminal.app returns, and it is what stops this
    module from acting. It is never upgraded into a best guess.
    """
    want = normalize_tty(tty)
    if want is None:
        return None
    # `to_thread`, not a direct call: `pgrep` is a blocking subprocess and
    # this runs on the voice loop.
    if not await asyncio.to_thread(_terminal_is_running):
        return None
    code, stdout, stderr = await _osascript(_ENUMERATE_SCRIPT, LOOKUP_TIMEOUT)
    if code != 0:
        log.warning(f"Terminal tab enumeration failed ({code}): {stderr.strip()}")
        return None
    for line in stdout.splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3:
            continue
        wid, idx, tab_tty = parts
        if normalize_tty(tab_tty) != want:
            continue
        try:
            return TerminalTab(window_id=int(wid), tab_index=int(idx), tty=want)
        except ValueError:
            continue
    return None


def _send_script(tab: TerminalTab, normalized: str) -> str:
    """The one script that activates, presses, and hands focus back.

    Three things are load-bearing:

    * It re-reads the tab's tty and ABORTS if it no longer matches. Tabs can
      close or be reordered between the lookup and the press; without this,
      a window id and index that pointed at the right session a moment ago
      could point at a different one now.
    * The frontmost application is captured BEFORE Terminal is activated and
      restored after, so a keypress the user asked for does not leave them
      staring at a terminal they were not using.
    * Only `key code` for Return/Escape, or `keystroke` of a single literal
      digit. `normalized` has already been through `normalize_key`, so the
      only values that can reach the interpolation are "return", "escape",
      or one character of "123456789" — nothing user-authored is ever
      substituted into this script.
    """
    if normalized == "return":
        press = f"key code {_RETURN_KEY_CODE}"
    elif normalized == "escape":
        press = f"key code {_ESCAPE_KEY_CODE}"
    else:
        press = f'keystroke "{normalized}"'
    return f'''
tell application "System Events"
    set priorApp to ""
    try
        set priorApp to name of first application process whose frontmost is true
    end try
end tell
tell application "Terminal"
    set targetWindow to missing value
    repeat with w in windows
        if (id of w) is {tab.window_id} then set targetWindow to w
    end repeat
    if targetWindow is missing value then return "gone"
    if (count of tabs of targetWindow) < {tab.tab_index} then return "gone"
    set targetTab to tab {tab.tab_index} of targetWindow
    set nowTty to ""
    try
        set nowTty to (tty of targetTab) as text
    end try
    if nowTty is not "{tab.tty}" then return "moved"
    activate
    set selected tab of targetWindow to targetTab
    set index of targetWindow to 1
end tell
delay 0.2
tell application "System Events"
    tell process "Terminal" to set frontmost to true
    {press}
end tell
delay 0.1
tell application "System Events"
    if priorApp is not "" and priorApp is not "Terminal" then
        try
            set frontmost of process priorApp to true
        end try
    end if
end tell
return "ok"
'''


async def answer(pid: int, key: str) -> str:
    """Press one key in the terminal that owns `pid`. Never raises.

    macOS targets an exact Terminal.app tab by tty. Linux targets only an
    exact tmux pane whose tty is proven to equal the process tty. Other
    terminal hosts fail closed as `not_found`.

    Only `sent` means an input event was actually delivered.
    """
    normalized = normalize_key(key)
    if normalized is None:
        log.warning(f"refusing a key outside the vocabulary: {key!r}")
        return BAD_KEY

    try:
        tty = await tty_for_pid_async(pid)
        if tty is None:
            return NO_TTY

        if sys.platform.startswith("linux"):
            return await _answer_linux_tmux(pid, tty, normalized)

        if sys.platform != "darwin":
            return NOT_FOUND

        # macOS path: exact tty -> Terminal.app tab -> re-check tty -> key.
        tab = await find_terminal_tab(tty)
        if tab is None:
            return NOT_FOUND

        code, stdout, stderr = await _osascript(
            _send_script(tab, normalized), SEND_TIMEOUT)

        if code != 0:
            if _is_permission_error(stderr):
                log.warning(
                    f"keystroke refused by macOS: {stderr.strip()}")
                return NOT_PERMITTED
            log.warning(
                f"keystroke script failed ({code}): {stderr.strip()}")
            return FAILED

        result = stdout.strip()
        if result == "ok":
            return SENT

        if result in ("gone", "moved"):
            log.warning(
                f"tab for {tty} was {result} at press time")
            return NOT_FOUND

        log.warning(
            f"unexpected keystroke script result: {result!r}")
        return FAILED

    except Exception as e:
        log.warning(
            f"answering a dialog for pid {pid} failed: {e}",
            exc_info=True)
        return FAILED
