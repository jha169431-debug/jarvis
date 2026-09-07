"""
JARVIS Notifier -- macOS notification fallback.

When JARVIS needs the user's attention but no browser tab is connected to
speak through, this posts a native macOS notification instead. See
actions.py for the project's usual pattern of driving `osascript`; this
module differs from it deliberately (see below) because the text here is
attacker-influenced.

Notification titles/messages surface a Claude Code session's title or its
last message -- text that originates in someone else's transcript, not
text JARVIS wrote itself. actions.py's `applescript_escape()` handles this
by escaping quotes/backslashes before interpolating into the script
*source*. That is fine for its callers, but here we take a stronger
approach: the script never contains the untrusted text at all. It is
written once as a fixed `on run argv` handler and read from stdin, and the
title/message/subtitle are passed as trailing argv strings, which
osascript hands to the script as plain data -- never parsed as AppleScript
source. There is no escaping step to get wrong because there is nothing to
escape: a value containing `" & do shell script "..." & "` is just a
string with those characters in it.
"""

import asyncio
import logging
import shutil
import sys

log = logging.getLogger("jarvis.notifier")

# A notification is a glance, not an essay -- keep it short. These bound
# what we pass to Notification Center regardless of how long the source
# text (a session title, a transcript line) actually is.
_TITLE_MAX = 120
_SUBTITLE_MAX = 120
_MESSAGE_MAX = 300

# osascript is usually near-instant; a wedged one must not hang the caller.
_TIMEOUT_SECONDS = 5.0

# Fixed script, no untrusted text ever enters this string. Title, message,
# and subtitle arrive purely via argv (see module docstring).
_NOTIFY_SCRIPT = """\
on run argv
    set theTitle to item 1 of argv
    set theMessage to item 2 of argv
    set theSubtitle to item 3 of argv
    if theSubtitle is "" then
        display notification theMessage with title theTitle
    else
        display notification theMessage with title theTitle subtitle theSubtitle
    end if
end run
"""


def _truncate(text: str, limit: int) -> str:
    """Bound text length for a glanceable notification, marking any cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"  # ellipsis


def _backend() -> str | None:
    """Return the native notification backend available on this platform."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        return "macos"

    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        return "linux"

    return None


def available() -> bool:
    """Whether a native notification backend is available."""
    return _backend() is not None


async def notify(title: str, message: str, *, subtitle: str = "") -> bool:
    """Post a native notification without ever raising into the caller."""
    try:
        backend = _backend()

        if backend is None:
            log.warning("notifier: notifications unavailable on this platform")
            return False

        safe_title = _truncate(str(title or ""), _TITLE_MAX)
        safe_message = _truncate(str(message or ""), _MESSAGE_MAX)
        safe_subtitle = _truncate(str(subtitle or ""), _SUBTITLE_MAX)

        try:
            if backend == "macos":
                proc = await asyncio.create_subprocess_exec(
                    "osascript",
                    "-",
                    safe_title,
                    safe_message,
                    safe_subtitle,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                communicate_input = _NOTIFY_SCRIPT.encode("utf-8")

            else:
                body = (
                    f"{safe_subtitle}\n{safe_message}"
                    if safe_subtitle
                    else safe_message
                )

                proc = await asyncio.create_subprocess_exec(
                    "notify-send",
                    "--app-name",
                    "JARVIS",
                    safe_title,
                    body,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                communicate_input = None

        except OSError as e:
            log.warning(f"notifier: failed to spawn notification backend: {e}")
            return False

        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(communicate_input),
                timeout=_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning("notifier: notification backend timed out, killing it")
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            return False

        if proc.returncode != 0:
            log.warning(
                f"notifier: backend exited {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()}"
            )
            return False

        return True

    except Exception as e:
        log.warning(f"notifier: unexpected error posting notification: {e}")
        return False
