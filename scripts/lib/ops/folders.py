"""Folder-level email listing using batch JXA."""

import json
from ..jxa import run_jxa_with_core, JXAError, enrich_with_content


def list_emails_in_folder(
    account_email: str,
    folder_name: str,
    limit: int = 12,
    include_content: bool = False,
) -> list[dict] | dict:
    """List emails in a specific folder from a specific account.

    Returns metadata only by default (~0.3 s). When include_content=True, adds a
    preview field with the first ~5000 chars from the search index.
    """
    safe_email = json.dumps(account_email)
    safe_folder = json.dumps(folder_name)
    effective_limit = limit if limit else 999999

    script = f"""
var acct = MailCore.getAccountByEmail({safe_email});
var mbox = MailCore.getMailbox(acct, {safe_folder});
var folderName = mbox.name();
var data = MailCore.fetchLimited(mbox,
    ["id", "subject", "sender", "dateReceived", "messageId"], {effective_limit});
var results = [];
var count = data.id.length;
for (var i = 0; i < count; i++) {{
    results.push({{
        subject: data.subject[i] || "",
        id: String(data.id[i]),
        date_received: MailCore.formatDate(data.dateReceived[i]) || "",
        sender: data.sender[i] || "",
        account_email: {safe_email},
        folder_name: folderName,
        rfc_message_id: data.messageId[i] || ""
    }});
}}
JSON.stringify(results);
"""
    try:
        results = run_jxa_with_core(script, timeout=60)
    except (JXAError, TimeoutError):
        return []

    if include_content and results:
        return enrich_with_content(results)
    return results
