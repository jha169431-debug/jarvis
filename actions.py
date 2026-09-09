"""
JARVIS Action Executor — platform-aware system actions.

Execute actions IMMEDIATELY, before generating any LLM response.
Each function returns {"success": bool, "confirmation": str}.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys

log = logging.getLogger("jarvis.actions")

async def _mark_terminal_as_jarvis(revert_after: float = 5.0):
    """Temporarily set the front Terminal window to Ocean theme, then revert.

    Shows the user JARVIS is active in that terminal. Reverts after revert_after seconds.
    """
    # Save the current profile, switch to Ocean, then revert
    script_save = (
        'tell application "Terminal"\n'
        '    return name of current settings of front window\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_save,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        original_profile = stdout.decode().strip()

        # Switch to Ocean
        script_set = (
            'tell application "Terminal"\n'
            '    set current settings of front window to settings set "Ocean"\n'
            'end tell'
        )
        proc2 = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_set,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()

        # Schedule revert
        if original_profile and original_profile != "Ocean":
            asyncio.get_event_loop().call_later(
                revert_after,
                lambda: asyncio.ensure_future(_revert_terminal_theme(original_profile))
            )
    except Exception:
        pass


async def _revert_terminal_theme(profile_name: str):
    """Revert a Terminal window back to its original profile."""
    escaped = applescript_escape(profile_name)
    script = (
        'tell application "Terminal"\n'
        f'    set current settings of front window to settings set "{escaped}"\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass


def applescript_escape(s: str) -> str:
    """Escape a string for safe embedding in an AppleScript double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", " ")



async def open_terminal(command: str = "") -> dict:
    """Open the native terminal and optionally run a command."""

    if sys.platform.startswith("linux"):
        terminal = (
            shutil.which("gnome-terminal")
            or shutil.which("x-terminal-emulator")
        )

        if not terminal:
            return {
                "success": False,
                "confirmation": "I couldn't find a terminal emulator, sir.",
            }

        if command:
            shell = shutil.which("bash") or "/bin/sh"

            if os.path.basename(terminal) == "gnome-terminal":
                argv = [terminal, "--", shell, "-lc", command]
            else:
                argv = [terminal, "-e", shell, "-lc", command]
        else:
            argv = [terminal]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as e:
            log.error(f"open_terminal could not launch: {e}")
            return {
                "success": False,
                "confirmation": "I had trouble opening Terminal, sir.",
            }

        success = proc.returncode == 0

        if not success:
            log.error(
                "open_terminal failed: "
                + stderr.decode(errors="replace")
            )

        return {
            "success": success,
            "confirmation": (
                "Terminal is open, sir."
                if success
                else "I had trouble opening Terminal, sir."
            ),
        }

    if sys.platform != "darwin":
        return {
            "success": False,
            "confirmation": "Terminal control isn't supported on this platform yet, sir.",
        }

    # Existing macOS implementation.
    if command:
        escaped = applescript_escape(command)
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "{escaped}"\n'
            "end tell"
        )
    else:
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            "end tell"
        )

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    success = proc.returncode == 0

    if not success:
        log.error(f"open_terminal failed: {stderr.decode()}")
    else:
        await _mark_terminal_as_jarvis()

    return {
        "success": success,
        "confirmation": (
            "Terminal is open, sir."
            if success
            else "I had trouble opening Terminal, sir."
        ),
    }


async def open_browser(url: str, browser: str = "chrome") -> dict:
    """Open URL in Chrome, Firefox, or the system browser."""

    requested = browser.lower()

    if sys.platform.startswith("linux"):
        if requested == "firefox":
            binary = shutil.which("firefox")
            app_name = "Firefox"
        else:
            binary = (
                shutil.which("google-chrome")
                or shutil.which("chromium")
                or shutil.which("chromium-browser")
            )
            app_name = "Chrome"

        if binary:
            argv = [binary, url]
        else:
            opener = shutil.which("xdg-open")

            if not opener:
                return {
                    "success": False,
                    "confirmation": f"{app_name} ran into a problem, sir.",
                }

            argv = [opener, url]
            app_name = "your browser"

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as e:
            log.error(f"open_browser could not launch: {e}")
            return {
                "success": False,
                "confirmation": f"{app_name} ran into a problem, sir.",
            }

        success = proc.returncode == 0

        if not success:
            log.error(
                f"open_browser ({app_name}) failed: "
                + stderr.decode(errors="replace")
            )

        return {
            "success": success,
            "confirmation": (
                f"Pulled that up in {app_name}, sir."
                if success
                else f"{app_name} ran into a problem, sir."
            ),
        }

    if sys.platform != "darwin":
        return {
            "success": False,
            "confirmation": "Browser control isn't supported on this platform yet, sir.",
        }

    # Existing macOS implementation.
    escaped_url = applescript_escape(url)

    if requested == "firefox":
        app_name = "Firefox"
        script = (
            'tell application "Firefox"\n'
            "    activate\n"
            f'    open location "{escaped_url}"\n'
            "end tell"
        )
    else:
        app_name = "Chrome"
        script = (
            'tell application "Google Chrome"\n'
            "    activate\n"
            f'    open location "{escaped_url}"\n'
            "end tell"
        )

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    success = proc.returncode == 0

    if not success:
        log.error(f"open_browser ({app_name}) failed: {stderr.decode()}")

    return {
        "success": success,
        "confirmation": (
            f"Pulled that up in {app_name}, sir."
            if success
            else f"{app_name} ran into a problem, sir."
        ),
    }

# Keep backward compat
async def open_chrome(url: str) -> dict:
    return await open_browser(url, "chrome")


async def get_chrome_tab_info() -> dict:
    """Read the current Chrome tab's title and URL when safely supported.

    macOS uses Chrome's AppleScript interface. Linux deliberately returns an
    empty result: an ordinary Chrome/Chromium process exposes no equivalent
    safe current-tab API unless it was explicitly launched with a debugging
    interface, which JARVIS must not assume or enable behind the user's back.
    """
    if sys.platform != "darwin":
        return {}

    script = (
        'tell application "Google Chrome"\n'
        "    set tabTitle to title of active tab of front window\n"
        "    set tabURL to URL of active tab of front window\n"
        '    return tabTitle & "|" & tabURL\n'
        "end tell"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            result = stdout.decode().strip()
            parts = result.split("|", 1)
            if len(parts) == 2:
                return {"title": parts[0], "url": parts[1]}
        return {}
    except Exception as e:
        log.warning(f"get_chrome_tab_info failed: {e}")
        return {}


# --- Opening code where the user actually reads it -----------------------
#
# The user's words: "maybe he should be able to open code files in VS Code or
# text editor." VS Code first when it is installed, the system default
# otherwise, so this still works on a Mac that has never had it.
#
# No AppleScript and no shell: `open` is exec'd with the path as its own argv
# entry, so a filename cannot be quoted out of a script the way it can out of
# an AppleScript string literal. Containment and the sensitive-file wall are
# the CALLER's job and have already run by the time this is reached — see
# server.tool_open_in_editor.

VSCODE_APP = "/Applications/Visual Studio Code.app"


def _vscode_command(path: str) -> list[str] | None:
    """The argv that opens `path` in VS Code, or None if it is not installed."""
    binary = shutil.which("code")
    if binary:
        return [binary, str(path)]
    if os.path.isdir(VSCODE_APP):
        return ["open", "-a", VSCODE_APP, str(path)]
    return None



async def open_in_editor(path: str) -> dict:
    """Open a file or directory in VS Code, else in the system default."""
    argv = _vscode_command(path)
    editor = "VS Code"

    if argv is None:
        if sys.platform == "darwin":
            argv = ["open", str(path)]
        elif sys.platform.startswith("linux"):
            opener = shutil.which("xdg-open")
            if not opener:
                return {
                    "success": False,
                    "editor": "your editor",
                    "confirmation": "I couldn't open an editor, sir.",
                }

            # Desktop launchers may remain attached to the GUI application.
            # Hand the path off and return immediately instead of awaiting the
            # lifetime of the user's editor.
            try:
                subprocess.Popen(
                    [opener, str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as e:
                log.error(f"open_in_editor could not launch: {e}")
                return {
                    "success": False,
                    "editor": "your editor",
                    "confirmation": "I couldn't open an editor, sir.",
                }

            return {
                "success": True,
                "editor": "your editor",
                "confirmation": "Opened that in your editor, sir.",
            }
        else:
            return {
                "success": False,
                "editor": "your editor",
                "confirmation": "I couldn't open an editor, sir.",
            }

        editor = "your editor"

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        success = proc.returncode == 0
    except OSError as e:
        log.error(f"open_in_editor could not launch: {e}")
        return {
            "success": False,
            "editor": editor,
            "confirmation": "I couldn't open an editor, sir.",
        }

    if not success:
        log.error(
            f"open_in_editor failed: "
            f"{stderr.decode(errors='replace')}"
        )

    return {
        "success": success,
        "editor": editor,
        "confirmation": (
            f"Opened that in {editor}, sir."
            if success
            else f"{editor} wouldn't open that, sir."
        ),
    }

def _generate_project_name(prompt: str) -> str:
    """Generate a kebab-case project folder name from the prompt."""
    # First: check for a quoted name like "tiktok-analytics-dashboard"
    quoted = re.search(r'"([^"]+)"', prompt)
    if quoted:
        name = quoted.group(1).strip()
        # Already kebab-case or close to it
        name = re.sub(r"[^a-zA-Z0-9\s-]", "", name).strip()
        if name:
            return re.sub(r"[\s]+", "-", name.lower())

    # Second: check for "called X" or "named X" pattern
    called = re.search(r'(?:called|named)\s+(\S+(?:[-_]\S+)*)', prompt, re.IGNORECASE)
    if called:
        name = re.sub(r"[^a-zA-Z0-9-]", "", called.group(1))
        if len(name) > 3:
            return name.lower()

    # Fallback: extract meaningful words
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt.lower()).split()
    skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and",
            "to", "of", "i", "want", "need", "new", "project", "directory", "called",
            "on", "desktop", "that", "application", "app", "full", "stack", "simple",
            "web", "page", "site", "named"}
    meaningful = [w for w in words if w not in skip and len(w) > 2][:4]
    return "-".join(meaningful) if meaningful else "jarvis-project"
