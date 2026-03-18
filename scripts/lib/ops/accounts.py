"""Account-level operations using batch JXA."""

import json
from ..jxa import run_jxa_with_core, JXAError, enrich_with_content


def list_accounts():
    """Get all logged in mail accounts (~0.15 s)."""
    try:
        return run_jxa_with_core("JSON.stringify(MailCore.listAccounts());")
    except (JXAError, TimeoutError):
        return []


def list_account_folders(account_email: str):
    """Get folder tree for a specific account (~1-2 s due to per-mailbox message counts)."""
    safe_email = json.dumps(account_email)
    script = f"""
var acct = MailCore.getAccountByEmail({safe_email});
var data = MailCore.listMailboxesWithCounts(acct);
JSON.stringify(data);
"""
    try:
        folders = run_jxa_with_core(script)
        return sorted(folders, key=lambda x: x.get("folder_path", x["folder_name"]).lower())
    except (JXAError, TimeoutError):
        return []


def list_recent_emails(most_recent_n_emails: int = 20, include_content: bool = False):
    """List recent emails from all account inboxes.

    Returns metadata only by default (~0.3 s). When include_content=True, adds a
    preview field with the first ~5000 chars from the search index.
    """
    limit = most_recent_n_emails if most_recent_n_emails else 999999
    script = f"""
var accounts = Mail.accounts();
var accNames = Mail.accounts.name();
var accEmails = Mail.accounts.emailAddresses();
var results = [];
var limit = {limit};

for (var a = 0; a < accounts.length; a++) {{
    var acct = accounts[a];
    var accEmail = accEmails[a].length > 0 ? accEmails[a][0] : accNames[a];
    var mboxNames = acct.mailboxes.name();
    var mboxes = acct.mailboxes();

    for (var m = 0; m < mboxNames.length; m++) {{
        if (mboxNames[m].toLowerCase() !== "inbox") continue;
        var mbox = mboxes[m];
        var folderName = mboxNames[m];
        var data = MailCore.fetchLimited(mbox,
            ["id", "subject", "sender", "dateReceived", "messageId"], limit);
        var count = data.id.length;
        for (var i = 0; i < count; i++) {{
            results.push({{
                id: String(data.id[i]),
                subject: data.subject[i] || "",
                sender: data.sender[i] || "",
                date_received: MailCore.formatDate(data.dateReceived[i]) || "",
                account_email: accEmail,
                folder_name: folderName,
                rfc_message_id: data.messageId[i] || ""
            }});
        }}
        break;
    }}
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
