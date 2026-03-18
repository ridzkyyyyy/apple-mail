"""Email and draft deletion operations."""

import json
import textwrap
from datetime import datetime, timezone
from ..applescript import validate_id, run_applescript, sync_mail_state
from .. import ASSETS_DIR

DELETION_LOG_PATH = ASSETS_DIR / "deletion-log.jsonl"
MAX_BATCH_SIZE = 50  # safety cap; override with force=True


def delete_draft(draft_id: str) -> dict:
    """Delete a draft email by its ID."""
    try:
        draft_id = validate_id(draft_id)
    except ValueError as e:
        return {"success": False, "message": str(e)}

    script = textwrap.dedent(
        f"""
        tell application "Mail"
            set targetId to {draft_id} as integer
            set foundMessage to missing value

            repeat with acc in accounts
                repeat with mbox in mailboxes of acc
                    ignoring case
                        set isDraft to (name of mbox contains "draft")
                    end ignoring
                    if isDraft then
                        set msgList to (messages of mbox whose id is targetId)
                        if (count of msgList) > 0 then
                            set foundMessage to item 1 of msgList
                            exit repeat
                        end if
                    end if
                end repeat
                if foundMessage is not missing value then exit repeat
            end repeat

            if foundMessage is missing value then
                return "DRAFT_NOT_FOUND"
            end if

            delete foundMessage
            return "SUCCESS"
        end tell
        """
    )

    try:
        result = run_applescript(script)
    except TimeoutError as e:
        return {"success": False, "message": str(e)}
    except RuntimeError as e:
        return {"success": False, "message": str(e)}

    output = result.stdout.strip()

    if output == "DRAFT_NOT_FOUND":
        return {"success": False, "message": f"draft with id {draft_id} not found"}
    elif output == "SUCCESS":
        sync_mail_state()
        return {"success": True, "message": "draft deleted successfully"}
    else:
        return {"success": False, "message": f"unexpected output: {output}"}


def _log_deletions(entries: list[dict]) -> list[str]:
    """Append deletion records to the audit log (JSONL format).

    Returns a list of warning strings (empty if logging succeeded).
    """
    warnings = []
    try:
        DELETION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DELETION_LOG_PATH, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        warnings.append(f"audit log write failed ({e}) — deletion proceeded without logging")
    return warnings


def delete_emails_batch(
    email_ids: list[str],
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Delete multiple emails, resilient to Exchange ID shifts.

    Strategy:
    1. Resolve integer IDs → RFC Message-ID + metadata via JXA (one call)
    2. Warn about flagged emails (skip unless force=True)
    3. Log what will be deleted to deletion-log.jsonl
    4. Delete in sub-batches of 6 using stable RFC Message-IDs
    5. Report unresolved IDs as not_found (never fall back to integer-ID delete)
    """
    from ..jxa import run_jxa_with_core, JXAError

    SUB_BATCH_SIZE = 6

    try:
        validated = [validate_id(eid) for eid in email_ids]
    except ValueError as e:
        return {"success": False, "message": str(e), "deleted": 0, "requested": 0, "not_found": []}

    if not validated:
        return {"success": False, "message": "no email ids provided", "deleted": 0, "requested": 0, "not_found": []}

    # Deduplicate input IDs (preserve order)
    seen = set()
    unique_validated = []
    for v in validated:
        if v not in seen:
            seen.add(v)
            unique_validated.append(v)
    validated = unique_validated

    # Batch size safety cap (checked after dedup for accurate count)
    if len(validated) > MAX_BATCH_SIZE and not force:
        return {
            "success": False,
            "message": f"batch size {len(validated)} (after dedup) exceeds safety cap of {MAX_BATCH_SIZE}. "
                       f"Pass force=True or use smaller batches.",
            "deleted": 0,
            "requested": len(validated),
            "not_found": [],
        }

    # Phase 1: resolve integer IDs → metadata (RFC ID, subject, sender, flagged)
    int_ids = [int(v) for v in validated]
    ids_json = ",".join(str(i) for i in int_ids)
    resolve_script = f"JSON.stringify(MailCore.resolveMessageDetails([{ids_json}]));"

    details_map = {}  # str(int_id) → {rfcId, subject, sender, flagged}
    try:
        result = run_jxa_with_core(resolve_script, timeout=30)
        if isinstance(result, dict):
            details_map = result
    except (JXAError, TimeoutError):
        pass  # resolution failed — all IDs will land in 'unresolved' and be reported as not_found

    # Phase 1b: check for flagged emails
    flagged_ids = []
    for v in validated:
        info = details_map.get(v, {})
        if isinstance(info, dict) and info.get("flagged"):
            flagged_ids.append(v)

    if flagged_ids and not force:
        flagged_subjects = []
        for fid in flagged_ids:
            info = details_map.get(fid, {})
            subj = info.get("subject", "?") if isinstance(info, dict) else "?"
            flagged_subjects.append(f"  id={fid}: {subj}")
        return {
            "success": False,
            "message": f"{len(flagged_ids)} flagged/starred email(s) in batch — refusing to delete. "
                       f"Remove flags first or pass force=True.\n" + "\n".join(flagged_subjects),
            "deleted": 0,
            "requested": len(validated),
            "not_found": [],
            "flagged_ids": flagged_ids,
        }

    # Split into resolved (have RFC ID) and unresolved (no RFC ID)
    # Deduplicate by RFC ID — multiple int IDs may map to the same RFC ID after a shift
    resolved = {}    # rfc_message_id → first original int_id
    unresolved = []  # int_ids without RFC Message-ID
    seen_rfc = set()
    for v in validated:
        info = details_map.get(v, {})
        rfc_id = info.get("rfcId", "") if isinstance(info, dict) else ""
        if rfc_id:
            if rfc_id not in seen_rfc:
                resolved[rfc_id] = v
                seen_rfc.add(rfc_id)
            # else: duplicate RFC ID — same email under a shifted int ID, skip
        else:
            unresolved.append(v)

    # Phase 2: audit log — record what we're about to delete
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entries = []
    for v in validated:
        info = details_map.get(v, {})
        if isinstance(info, dict):
            log_entries.append({
                "timestamp": timestamp,
                "int_id": v,
                "rfc_message_id": info.get("rfcId", ""),
                "subject": info.get("subject", ""),
                "sender": info.get("sender", ""),
                "flagged": info.get("flagged", False),
                "dry_run": dry_run,
            })
        else:
            log_entries.append({
                "timestamp": timestamp,
                "int_id": v,
                "rfc_message_id": "",
                "subject": "",
                "sender": "",
                "flagged": False,
                "dry_run": dry_run,
            })
    log_warnings = _log_deletions(log_entries)

    # Dry-run: return what would be deleted without actually deleting
    if dry_run:
        preview = []
        for entry in log_entries:
            preview.append({
                "id": entry["int_id"],
                "subject": entry["subject"],
                "sender": entry["sender"],
                "rfc_message_id": entry["rfc_message_id"],
                "flagged": entry["flagged"],
            })
        result = {
            "success": True,
            "dry_run": True,
            "deleted": 0,
            "requested": len(validated),
            "not_found": [],
            "would_delete": len(preview),
            "emails": preview,
            "message": f"dry run — would delete {len(preview)} email(s), nothing was deleted",
            "audit_log": str(DELETION_LOG_PATH),
        }
        if log_warnings:
            result["warnings"] = log_warnings
        return result

    total_deleted = 0
    all_not_found = []

    # Phase 3: delete resolved messages by RFC Message-ID in sub-batches
    rfc_ids = list(resolved.keys())
    for i in range(0, len(rfc_ids), SUB_BATCH_SIZE):
        chunk = rfc_ids[i:i + SUB_BATCH_SIZE]
        result = _delete_by_rfc_ids(chunk)
        total_deleted += result["deleted"]
        for nf_rfc in result["not_found"]:
            orig = resolved.get(nf_rfc, nf_rfc)
            all_not_found.append(orig)

        # sync between sub-batches (not after the last one — we sync at the end)
        if i + SUB_BATCH_SIZE < len(rfc_ids):
            sync_mail_state(delay_seconds=0.5)

    # Phase 4: unresolved IDs — refuse to delete by integer ID (unsafe)
    # Previously this fell back to delete_email(int_id) which uses the volatile
    # integer ID and could delete the WRONG email after an Exchange sync shift.
    # Now we report them as not_found and let the caller re-list for fresh IDs.
    for int_id in unresolved:
        all_not_found.append(int_id)

    if total_deleted > 0:
        sync_mail_state()

    # Build list of what was actually deleted (subjects/senders for agent verification)
    deleted_emails = []
    for rfc_id, orig_int_id in resolved.items():
        if orig_int_id not in all_not_found:
            info = details_map.get(orig_int_id, {})
            if isinstance(info, dict):
                deleted_emails.append({
                    "id": orig_int_id,
                    "subject": info.get("subject", ""),
                    "sender": info.get("sender", ""),
                    "rfc_message_id": info.get("rfcId", ""),
                })

    # Build descriptive message distinguishing unresolved from delete-not-found
    msg_parts = [f"deleted {total_deleted}/{len(validated)} emails"]
    if unresolved:
        msg_parts.append(
            f"{len(unresolved)} could not be resolved to stable RFC IDs "
            f"(Exchange ID shift or JXA timeout — re-list for fresh IDs)"
        )
    rfc_not_found = [nf for nf in all_not_found if nf not in unresolved]
    if rfc_not_found:
        msg_parts.append(f"{len(rfc_not_found)} resolved but not found during deletion")

    return {
        "success": total_deleted > 0,
        "deleted": total_deleted,
        "requested": len(validated),
        "not_found": all_not_found,
        "deleted_emails": deleted_emails,
        "message": ", ".join(msg_parts),
        "audit_log": str(DELETION_LOG_PATH),
        **({"warnings": log_warnings} if log_warnings else {}),
    }


def _delete_by_rfc_ids(rfc_ids: list[str]) -> dict:
    """Delete a sub-batch of emails by their RFC Message-ID headers."""
    from ..applescript import escape_applescript

    escaped = [escape_applescript(mid) for mid in rfc_ids]
    as_list = ", ".join(f'"{e}"' for e in escaped)

    script = textwrap.dedent(
        f"""
        tell application "Mail"
            set targetMsgIds to {{{as_list}}}
            set deletedCount to 0
            set notFoundIds to {{}}

            repeat with msgId in targetMsgIds
                set foundMessage to missing value

                considering case
                    repeat with acc in accounts
                        repeat with mbox in mailboxes of acc
                            set msgList to (messages of mbox whose message id is msgId)
                            if (count of msgList) > 0 then
                                set foundMessage to item 1 of msgList
                                exit repeat
                            end if
                        end repeat
                        if foundMessage is not missing value then exit repeat
                    end repeat
                end considering

                if foundMessage is not missing value then
                    delete foundMessage
                    set deletedCount to deletedCount + 1
                else
                    set end of notFoundIds to (msgId as string)
                end if
            end repeat

            set oldDelimiters to AppleScript's text item delimiters
            set AppleScript's text item delimiters to ","
            set notFoundStr to notFoundIds as string
            set AppleScript's text item delimiters to oldDelimiters

            return (deletedCount as string) & "|||" & notFoundStr
        end tell
        """
    )

    try:
        result = run_applescript(script)
    except TimeoutError:
        return {"deleted": 0, "not_found": rfc_ids}
    except RuntimeError:
        return {"deleted": 0, "not_found": rfc_ids}

    output = result.stdout.strip()
    parts = output.split("|||")
    deleted = int(parts[0]) if parts[0].isdigit() else 0
    nf = [x.strip() for x in parts[1].split(",") if x.strip()] if len(parts) > 1 and parts[1].strip() else []

    return {"deleted": deleted, "not_found": nf}
