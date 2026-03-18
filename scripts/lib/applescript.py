"""AppleScript execution, validation, and escaping utilities for Apple Mail."""

import re
import subprocess
import time
from pathlib import Path


def validate_id(value: str, label: str = "id") -> str:
    """Validate that an id is numeric to prevent AppleScript injection."""
    if not re.match(r"^\d+$", value.strip()):
        raise ValueError(f"invalid {label}: {value!r} — must be numeric")
    return value.strip()


def escape_applescript(text: str) -> str:
    """Escape a string for safe interpolation into AppleScript."""
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return text


def run_applescript(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Execute an AppleScript via osascript with timeout and error checking."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"applescript timed out after {timeout}s")

    if result.returncode != 0:
        raise RuntimeError(f"applescript error: {result.stderr.strip()}")

    return result


def validate_attachments(attachments: list[str]) -> tuple[list[str], str]:
    """Validate attachment file paths, returns (valid_paths, error_msg)."""
    if not attachments:
        return [], ""

    valid_paths = []
    for path in attachments:
        p = Path(path).expanduser()
        if not p.exists():
            return [], f"attachment not found: {path}"
        if not p.is_file():
            return [], f"attachment is not a file: {path}"
        valid_paths.append(str(p.absolute()))

    return valid_paths, ""


def build_recipients(recipients: list[str], recipient_type: str, target: str = "newMessage") -> str:
    """Build AppleScript recipient section."""
    if not recipients:
        return ""

    lines = [
        f'tell {target} to make new {recipient_type} recipient with properties {{address:"{escape_applescript(addr)}"}}'
        for addr in recipients
    ]
    return "\n            ".join(lines) + "\n            "


def build_attachments(attachment_paths: list[str], target: str = "newMessage") -> str:
    """Build AppleScript attachment section."""
    if not attachment_paths:
        return ""

    lines = [
        f'tell {target} to make new attachment with properties {{file name:"{escape_applescript(path)}"}}'
        for path in attachment_paths
    ]
    return "\n            ".join(lines) + "\n            "


def sync_mail_state(delay_seconds: float = 0.3):
    """Synchronize with Mail.app to ensure operations complete and IDs are stable."""
    script = """
        tell application "Mail"
            synchronize
        end tell
    """
    try:
        run_applescript(script, timeout=10)
    except (TimeoutError, RuntimeError):
        pass
    time.sleep(delay_seconds)
