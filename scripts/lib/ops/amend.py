"""Email amendment operations — direct .emlx file editing.

Apple Mail's scripting bridge marks `subject` as read-only on received
messages (error -10006).  The only reliable way to change a subject is
to edit the .emlx file on disk and force Mail.app to re-read it.

Requires Full Disk Access for the process running this code.
"""

import email.header
import json
import os
import plistlib
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..applescript import validate_id, run_applescript
from ..jxa import run_jxa_with_core, JXAError
from .. import ASSETS_DIR
from ..search_index.disk import find_mail_directory

_SUBJECT_RE = re.compile(
    rb"^Subject:[ \t].*?(?=\r?\n(?![ \t]))",
    re.MULTILINE | re.DOTALL,
)

_AMENDMENT_LOG = ASSETS_DIR / "amendment-log.jsonl"


class MailQuitError(Exception):
    """Raised when Mail.app could not be confirmed quit."""


def amend_subject(email_id: str, new_subject: str, dry_run: bool = False, _metadata: dict = None) -> dict:
    """Amend the subject of any email (received or draft) by its integer ID.

    Workflow:
      1. Use JXA to fetch current metadata (subject, sender, folder) for feedback.
      2. If --dry-run, return the preview without touching anything.
      3. Locate the .emlx file on disk.
      4. Quit Mail.app so it releases file handles and the Envelope Index.
      5. Rewrite the Subject header in the MIME content, update the byte count
         on line 1, and update the plist footer's <key>subject</key>.
      6. Relaunch Mail.app.

    Args:
      _metadata: Pre-fetched metadata dict from _fetch_metadata_jxa. Internal
                 use only — lets add_label skip the redundant JXA round-trip.

    Caveats:
      - IMAP/Exchange: the server holds the canonical subject. A mailbox
        rebuild or full sync may revert the change. Works reliably for
        local ("On My Mac") and POP accounts.
      - Mail.app must be quit during the edit. This function handles that
        automatically (quit → edit → relaunch).
    """
    try:
        email_id = validate_id(email_id, "email_id")
    except ValueError as e:
        return {"success": False, "message": str(e)}

    if not new_subject or not new_subject.strip():
        return {"success": False, "message": "new_subject is required"}

    # Sanitize newlines — a bare \r\n in the subject could inject headers
    new_subject = new_subject.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()

    eid = int(email_id)

    # --- Phase 1: fetch metadata via JXA for feedback ---
    if _metadata:
        metadata, meta_error = _metadata, None
    else:
        metadata, meta_error = _fetch_metadata_jxa(eid)
    if meta_error:
        return {"success": False, "message": meta_error}
    if metadata is None:
        return {
            "success": False,
            "message": f"email with id {eid} not found",
        }

    original_subject = metadata.get("subject", "")
    sender = metadata.get("sender", "")
    folder = metadata.get("folder", "")
    account = metadata.get("account", "")
    account_type = metadata.get("account_type", "")

    # --- Dry run: return preview without touching anything ---
    if dry_run:
        # Pre-check FDA by attempting to locate the .emlx
        emlx_path = _find_emlx(eid)
        resp = {
            "success": True,
            "message": "dry run — no changes made",
            "dry_run": True,
            "email_id": email_id,
            "original_subject": original_subject,
            "new_subject": new_subject,
            "sender": sender,
            "folder": folder,
            "audit_log": str(_AMENDMENT_LOG),
        }
        dry_warnings = []
        if emlx_path is None:
            dry_warnings.append(
                "could not locate .emlx file on disk — the real operation will fail. "
                "ensure Full Disk Access is granted to Terminal/Cursor in "
                "System Settings > Privacy & Security > Full Disk Access."
            )
        if _is_synced_account(account_type):
            dry_warnings.append(
                "this email is in a synced (IMAP/Exchange) account. "
                "a mailbox rebuild or server sync may revert the subject change."
            )
        if _is_mail_running():
            dry_warnings.append(
                "Mail.app will be quit and relaunched during the real operation. "
                "any open compose windows with unsaved drafts will be lost."
            )
        if dry_warnings:
            resp["warnings"] = dry_warnings
        return resp

    # --- Phase 2: locate .emlx on disk ---
    emlx_path = _find_emlx(eid)
    if emlx_path is None:
        return {
            "success": False,
            "message": (
                f"could not locate .emlx file for email {eid} on disk. "
                "this requires Full Disk Access — grant it to Terminal/Cursor in "
                "System Settings > Privacy & Security > Full Disk Access, then retry."
            ),
        }

    # Verify the .emlx subject matches JXA metadata to prevent wrong-email edit
    disk_subject = _read_subject_from_emlx(emlx_path)
    if disk_subject and original_subject and disk_subject != original_subject:
        return {
            "success": False,
            "message": (
                f"safety check failed: .emlx subject '{disk_subject}' does not match "
                f"Mail.app subject '{original_subject}' for id {eid}. "
                "the .emlx file may belong to a different email. aborting."
            ),
        }

    # --- Phase 3: backup, rewrite, verify ---
    backup_path = emlx_path.with_suffix(".emlx.bak")
    try:
        shutil.copy2(str(emlx_path), str(backup_path))
    except OSError as e:
        return {"success": False, "message": f"failed to create backup: {e}"}

    warnings = []

    try:
        _quit_mail()

        _rewrite_emlx_subject(emlx_path, new_subject)

        # Verify the rewrite by re-reading
        verified = _read_subject_from_emlx(emlx_path)
        if not verified:
            warnings.append("could not verify the rewritten subject — check manually in Mail.app")

        if not _launch_mail():
            warnings.append("Mail.app could not be relaunched — open it manually")

        # Clean up backup on success (atomic rename to avoid partial delete)
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass

    except MailQuitError as e:
        # Mail wouldn't quit — don't touch the file, don't need to restore
        return {"success": False, "message": str(e), "warnings": warnings}

    except Exception as e:
        # Restore from backup atomically
        try:
            if backup_path.exists():
                os.rename(str(backup_path), str(emlx_path))
        except OSError:
            warnings.append(f"restore from backup failed — manual recovery: {backup_path}")

        if not _launch_mail():
            warnings.append("Mail.app could not be relaunched — open it manually")

        return {"success": False, "message": f"rewrite failed: {e}", "warnings": warnings}

    # --- Phase 4: warnings and response ---
    if _is_synced_account(account_type):
        warnings.append(
            "this email is in a synced (IMAP/Exchange) account. "
            "a mailbox rebuild or server sync may revert the subject change. "
            "the change is permanent for local/POP accounts."
        )

    if verified and verified != new_subject:
        warnings.append(
            f"verified subject '{verified}' differs from requested '{new_subject}'"
        )

    # Audit log
    _log_amendment(email_id, original_subject, verified or new_subject, sender, folder)

    # Update search index so searches reflect the new subject
    _update_index_subject(eid, verified or new_subject)

    resp = {
        "success": True,
        "message": "subject amended successfully",
        "email_id": email_id,
        "original_subject": original_subject,
        "new_subject": verified or new_subject,
        "sender": sender,
        "folder": folder,
        "audit_log": str(_AMENDMENT_LOG),
    }
    if warnings:
        resp["warnings"] = warnings

    return resp


def add_label(email_id: str, label: str, dry_run: bool = False) -> dict:
    """Prepend a [label] tag to an email's subject.

    Produces: "[label] original subject"
    Delegates to amend_subject for the actual edit.
    If the subject already starts with "[label] " (case-insensitive), returns
    success with no change.
    """
    try:
        email_id = validate_id(email_id, "email_id")
    except ValueError as e:
        return {"success": False, "message": str(e)}

    if not label or not label.strip():
        return {"success": False, "message": "label is required"}

    # Strip surrounding brackets if user passed them (e.g. "[done]" → "done")
    label = label.strip().strip("[]")
    if not label:
        return {"success": False, "message": "label is required (was empty after stripping brackets)"}

    eid = int(email_id)

    # Fetch current subject to build the new one
    metadata, meta_error = _fetch_metadata_jxa(eid)
    if meta_error:
        return {"success": False, "message": meta_error}
    if metadata is None:
        return {"success": False, "message": f"email with id {eid} not found"}

    original_subject = metadata.get("subject", "")
    sender = metadata.get("sender", "")
    folder = metadata.get("folder", "")

    # Already labelled — case-insensitive check with flexible spacing
    if re.match(rf"^\[{re.escape(label)}\]\s", original_subject, re.IGNORECASE) or \
       original_subject.lower() == f"[{label}]".lower():
        return {
            "success": True,
            "message": f"email already has [{label}] label — no change needed",
            "email_id": email_id,
            "original_subject": original_subject,
            "new_subject": original_subject,
            "sender": sender,
            "folder": folder,
            "label": label,
        }

    new_subject = f"[{label}] {original_subject}" if original_subject else f"[{label}]"

    result = amend_subject(email_id, new_subject, dry_run=dry_run, _metadata=metadata)

    # Enrich the response with label context
    if isinstance(result, dict):
        result["label"] = label
        if result.get("success") and not result.get("dry_run"):
            result["message"] = f"label [{label}] added successfully"

    return result


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _fetch_metadata_jxa(eid: int) -> tuple[dict | None, str | None]:
    """Fetch email metadata via JXA for feedback purposes.

    Returns (metadata_dict, None) on success, or (None, error_message) on failure.
    Distinguishes between 'not found' and 'JXA timeout/error'.
    """
    script = f"""
var msg = MailCore.findMessageAcrossAccounts({eid});
if (!msg) {{
    JSON.stringify({{found: false}});
}} else {{
    var mbox = msg.mailbox();
    var acc = mbox.account();
    var accEmail = "";
    try {{
        var addrs = acc.emailAddresses();
        accEmail = addrs.length > 0 ? addrs[0] : acc.name();
    }} catch(e) {{ accEmail = acc.name(); }}

    // Detect account type from the account's server type
    var accType = "unknown";
    try {{ accType = acc.accountType(); }} catch(e) {{}}

    JSON.stringify({{
        found: true,
        subject: msg.subject() || "",
        sender: msg.sender() || "",
        folder: mbox.name(),
        account: accEmail,
        account_type: accType
    }});
}}
"""
    try:
        result = run_jxa_with_core(script, timeout=15)
    except TimeoutError:
        return None, "could not fetch email metadata — Mail.app timed out, retry in a few seconds"
    except JXAError as e:
        return None, f"could not fetch email metadata — JXA error: {e}"

    if not result or not result.get("found"):
        return None, None  # genuinely not found

    result.pop("found", None)
    return result, None


def _find_emlx(eid: int) -> Path | None:
    """Locate the .emlx file for a given message ID on disk.

    Strategy:
      1. Check the search index DB for a cached emlx_path (fast, no FDA needed for DB).
      2. Fall back to rglob scan of ~/Library/Mail/ (needs FDA).
    """
    # Strategy 1: check search index DB for cached path
    path = _find_emlx_from_index(eid)
    if path:
        try:
            if path.exists():
                return path
        except PermissionError:
            pass  # FDA not granted — fall through to rglob

    # Strategy 2: rglob scan (needs Full Disk Access)
    try:
        mail_dir = find_mail_directory()
    except (FileNotFoundError, PermissionError):
        return None

    for p in mail_dir.rglob(f"{eid}.emlx"):
        if ".partial.emlx" not in p.name:
            return p
    return None


def _find_emlx_from_index(eid: int) -> Path | None:
    """Look up the emlx_path from the search index DB."""
    try:
        from ..search_index.schema import DB_PATH
        import sqlite3

        if not DB_PATH.exists():
            return None

        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        try:
            row = conn.execute(
                "SELECT emlx_path FROM emails WHERE message_id = ? AND emlx_path != ''",
                (eid,),
            ).fetchone()
            if row and row[0]:
                return Path(row[0])
        finally:
            conn.close()
    except Exception:
        pass
    return None


def _rewrite_emlx_subject(emlx_path: Path, new_subject: str) -> None:
    """Rewrite the Subject header in an .emlx file.

    Updates three things:
      1. The Subject: header in the MIME content
      2. The byte count on line 1
      3. The <key>subject</key> in the XML plist footer
    """
    raw = emlx_path.read_bytes()

    # --- Split into three sections ---
    nl = raw.find(b"\n")
    if nl == -1:
        raise ValueError(f"malformed .emlx: no newline found in {emlx_path}")

    old_byte_count = int(raw[:nl].strip())
    mime_bytes = raw[nl + 1 : nl + 1 + old_byte_count]
    plist_bytes = raw[nl + 1 + old_byte_count:]

    # --- 1. Replace Subject in MIME headers ---
    new_subject_header = b"Subject: " + _encode_subject(new_subject)
    new_mime_bytes, count = _SUBJECT_RE.subn(new_subject_header, mime_bytes, count=1)

    if count == 0:
        # No Subject header found — insert one after the first header line
        # Detect line ending convention from the file
        line_end = b"\r\n" if b"\r\n" in mime_bytes[:200] else b"\n"
        first_nl = mime_bytes.find(b"\n")
        if first_nl == -1:
            raise ValueError("malformed MIME: no newline in headers")
        new_mime_bytes = (
            mime_bytes[: first_nl + 1]
            + new_subject_header + line_end
            + mime_bytes[first_nl + 1 :]
        )

    # --- 2. Recompute byte count ---
    new_byte_count = len(new_mime_bytes)

    # --- 3. Update plist footer ---
    new_plist_bytes = _update_plist_subject(plist_bytes, new_subject)

    # --- Atomic write: temp file → fsync → rename ---
    output = (
        str(new_byte_count).encode("ascii") + b"\n"
        + new_mime_bytes
        + new_plist_bytes
    )

    tmp_path = emlx_path.with_suffix(".emlx.tmp")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(output)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            # fd is closed by os.fdopen's context manager
            raise
        os.rename(str(tmp_path), str(emlx_path))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _encode_subject(subject: str) -> bytes:
    """Encode a subject string for the Subject: header.

    ASCII subjects stay plain; non-ASCII gets RFC 2047 UTF-8 encoding.
    """
    try:
        return subject.encode("ascii")
    except UnicodeEncodeError:
        h = email.header.Header(subject, charset="utf-8")
        return h.encode().encode("ascii")


def _update_plist_subject(plist_bytes: bytes, new_subject: str) -> bytes:
    """Update the <key>subject</key> value in the XML plist footer.

    Uses regex replacement to preserve the original plist structure/key order
    rather than plistlib which reorders keys.
    """
    stripped = plist_bytes.strip()
    if not stripped:
        return plist_bytes

    # Regex replacement — preserves original structure and key order
    pattern = re.compile(
        rb"(<key>subject</key>\s*<string>)(.*?)(</string>)",
        re.DOTALL,
    )
    escaped_subject = (
        new_subject
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .encode("utf-8")
    )
    new_plist, n = pattern.subn(rb"\1" + escaped_subject + rb"\3", plist_bytes, count=1)
    if n > 0:
        return new_plist

    # Fallback: try plistlib if regex didn't match (unusual plist format)
    try:
        plist = plistlib.loads(stripped)
        if "subject" in plist:
            plist["subject"] = new_subject
            return b"\n" + plistlib.dumps(plist, fmt=plistlib.FMT_XML)
    except Exception:
        pass

    return plist_bytes


def _read_subject_from_emlx(emlx_path: Path) -> str:
    """Read back the Subject header from an .emlx file for verification."""
    try:
        raw = emlx_path.read_bytes()
        nl = raw.find(b"\n")
        byte_count = int(raw[:nl].strip())
        mime_bytes = raw[nl + 1 : nl + 1 + byte_count]
        raw_value = _extract_subject_raw(mime_bytes)
        if not raw_value:
            return ""
        decoded = email.header.decode_header(raw_value.decode("ascii", errors="replace"))
        return str(email.header.make_header(decoded))
    except Exception:
        return ""


def _extract_subject_raw(mime_bytes: bytes) -> bytes:
    """Extract the raw Subject header value from MIME bytes."""
    m = _SUBJECT_RE.search(mime_bytes)
    if m:
        line = m.group(0)
        return line.split(b":", 1)[1].strip()
    return b""


def _is_mail_running() -> bool:
    """Check if Mail.app is currently running."""
    try:
        result = run_applescript(
            'tell application "System Events" to return (name of processes) contains "Mail"',
            timeout=5,
        )
        return "true" in result.stdout.lower()
    except (TimeoutError, RuntimeError):
        return False


def _quit_mail():
    """Quit Mail.app and wait for it to fully exit.

    Raises MailQuitError if Mail could not be confirmed quit after retries.
    Skips entirely if Mail is not running.
    """
    if not _is_mail_running():
        return

    try:
        run_applescript('tell application "Mail" to quit', timeout=10)
    except (TimeoutError, RuntimeError):
        pass

    # Wait for Mail to actually quit
    for _ in range(15):
        time.sleep(0.5)
        if not _is_mail_running():
            return

    raise MailQuitError(
        "Mail.app could not be quit after 7.5 seconds — close it manually and retry"
    )


def _launch_mail() -> bool:
    """Relaunch Mail.app. Returns True on success, False on failure."""
    try:
        run_applescript('tell application "Mail" to activate', timeout=15)
        time.sleep(1)
        return True
    except (TimeoutError, RuntimeError):
        return False


def _is_synced_account(account_type: str) -> bool:
    """Check if the account type is IMAP or Exchange (synced with server)."""
    if not account_type:
        return True  # assume synced if unknown — safer to warn
    t = account_type.lower()
    local_types = {"pop", "local", "on my mac"}
    return t not in local_types


def _log_amendment(
    email_id: str,
    original_subject: str,
    new_subject: str,
    sender: str,
    folder: str,
):
    """Append an entry to the amendment audit log."""
    try:
        _AMENDMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": "amend-subject",
            "email_id": email_id,
            "original_subject": original_subject,
            "new_subject": new_subject,
            "sender": sender,
            "folder": folder,
        }
        with open(_AMENDMENT_LOG, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass  # logging failure should never block the operation


def _update_index_subject(eid: int, new_subject: str):
    """Update the subject in the search index DB after a successful amend."""
    try:
        from ..search_index.schema import DB_PATH
        import sqlite3

        if not DB_PATH.exists():
            return

        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        try:
            conn.execute(
                "UPDATE emails SET subject = ? WHERE message_id = ?",
                (new_subject, eid),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # best-effort — index will self-heal on next build-index
