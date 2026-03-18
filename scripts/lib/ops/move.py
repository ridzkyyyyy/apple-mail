"""Email move operations."""

import textwrap
from ..applescript import validate_id, escape_applescript, run_applescript, sync_mail_state

TODOS_FOLDER = "📝todos"


def _folder_search_applescript(to_folder: str) -> str:
    """Generate AppleScript to find a mailbox by leaf name (BFS) or path (segment walk)."""
    segments = [s for s in to_folder.split("/") if s]

    if len(segments) == 1:
        name = escape_applescript(segments[0])
        return textwrap.dedent(f"""
            set targetMailbox to missing value
            set mailboxQueue to mailboxes of sourceAccount
            repeat while (count of mailboxQueue) > 0
                set currentMbox to item 1 of mailboxQueue
                try
                    ignoring case
                        set folderMatch to ((name of currentMbox) is "{name}")
                    end ignoring
                    if folderMatch then
                        set targetMailbox to currentMbox
                        exit repeat
                    end if
                    try
                        repeat with subMbox in mailboxes of currentMbox
                            set end of mailboxQueue to subMbox
                        end repeat
                    end try
                end try
                set mailboxQueue to rest of mailboxQueue
            end repeat
        """)

    lines = []
    for i, seg in enumerate(segments):
        seg_escaped = escape_applescript(seg)
        parent = "sourceAccount" if i == 0 else f"seg{i - 1}"
        lines.append(f"""
            set seg{i} to missing value
            repeat with mbox in mailboxes of {parent}
                try
                    ignoring case
                        if (name of mbox) is "{seg_escaped}" then
                            set seg{i} to mbox
                            exit repeat
                        end if
                    end ignoring
                end try
            end repeat
            if seg{i} is missing value then return "FOLDER_NOT_FOUND"
        """)
    lines.append(f"            set targetMailbox to seg{len(segments) - 1}")
    return "\n".join(lines)


def move_email(email_id: str, to_folder: str, delay_seconds: int = 3) -> dict:
    """Move an email to a folder, verified by RFC Message-ID arrival in target.

    Accepts both leaf names (e.g. "paperworks") and full paths
    (e.g. "📂own-dirs/paperworks"). paths walk each segment explicitly,
    avoiding BFS name-collision issues with ghost folders.
    """
    try:
        email_id = validate_id(email_id, "email_id")
    except ValueError as e:
        return {"success": False, "message": str(e)}

    segments = [s for s in to_folder.split("/") if s]
    if not segments:
        return {"success": False, "message": "to_folder is empty"}

    folder_search = _folder_search_applescript(to_folder)

    script = textwrap.dedent(
        f"""
        tell application "Mail"
            set targetId to {email_id} as integer
            set foundMessage to missing value
            set sourceAccount to missing value

            repeat with acc in accounts
                repeat with mbox in mailboxes of acc
                    try
                        set msgList to (messages of mbox whose id is targetId)
                        if (count of msgList) > 0 then
                            set foundMessage to item 1 of msgList
                            set sourceAccount to acc
                            exit repeat
                        end if
                    end try
                end repeat
                if foundMessage is not missing value then exit repeat
            end repeat

            if foundMessage is missing value then
                return "EMAIL_NOT_FOUND"
            end if

            set rfcId to message id of foundMessage
            if rfcId is missing value or rfcId is "" then
                return "NO_RFC_ID"
            end if

            {folder_search}

            if targetMailbox is missing value then
                return "FOLDER_NOT_FOUND"
            end if

            move foundMessage to targetMailbox
            try
                synchronize with sourceAccount
            end try
            delay {delay_seconds}

            set arrivedInTarget to (messages of targetMailbox whose message id is rfcId)
            if (count of arrivedInTarget) > 0 then
                return "SUCCESS"
            else
                return "MOVE_FAILED"
            end if
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

    match output:
        case "EMAIL_NOT_FOUND":
            return {
                "success": False,
                "message": f"email {email_id} not found -- re-list the folder for fresh IDs and retry",
            }
        case "NO_RFC_ID":
            return {
                "success": False,
                "message": f"email {email_id} has no RFC Message-ID; move aborted (cannot verify arrival)",
            }
        case "FOLDER_NOT_FOUND":
            return {
                "success": False,
                "message": f"folder '{to_folder}' not found",
                "recovery": "ask user to create it in outlook web app (outlook.office.com), then retry",
            }
        case "MOVE_FAILED":
            sync_mail_state(delay_seconds=1.0)
            return {
                "success": False,
                "message": f"move to '{to_folder}' reverted by exchange -- folder is likely local-only",
                "recovery": "ask user to delete the ghost folder and recreate it server-side in OWA, then retry",
            }
        case "SUCCESS":
            sync_mail_state(delay_seconds=1.0)
            return {"success": True, "message": f"moved to {to_folder}"}
        case _:
            return {"success": False, "message": f"unexpected applescript output: {output}"}


def move_to_todos(email_id: str, delay_seconds: int = 3) -> dict:
    """Shortcut: move a single email to the 📝todos folder by ID.

    Delegates to move_email with the hardcoded TODOS_FOLDER name so agents
    don't need to remember or type the emoji folder name.
    """
    return move_email(email_id, TODOS_FOLDER, delay_seconds=delay_seconds)
