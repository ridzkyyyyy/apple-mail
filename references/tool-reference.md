# Apple Mail Tool Reference

Detailed parameter documentation and return shapes for each CLI command.

All commands are invoked via `scripts/mail.sh <command> [args]`.
All output is JSON with the response contract: `{success, data, error, warnings, meta}`.

---

## server-info

No parameters. Returns skill version and metadata.

```json
{"name": "apple-mail-skill", "version": "1.0.0", "total_commands": 23}
```

## check-health

No parameters. Returns `{success: bool, message: str}`.

## list-accounts

No parameters. Returns list of account objects:

```json
[{"name": "Exchange", "user": "jdoe", "emails": "me@example.com,alias@example.com"}]
```

## list-folders

| Param             | Required | Description           |
| ----------------- | -------- | --------------------- |
| `--account EMAIL` | yes      | Account email address |

Returns sorted list of folders:

```json
[{"folder_name": "Inbox", "email_count": 142, "folder_path": "Exchange/Inbox"}]
```

## list-recent

| Param               | Required | Default | Description                   |
| ------------------- | -------- | ------- | ----------------------------- |
| `--limit N`         | no       | 20      | Max emails per inbox          |
| `--include-content` | no       | false   | Add preview from search index |

Without `--include-content`: returns list of email dicts.
With `--include-content`: returns enrichment wrapper:

```json
{
  "emails": [
    {
      "id": "1234", "subject": "...", "sender": "...", "date_received": "...",
      "account_email": "...", "folder_name": "...",
      "preview": "first 5000 chars...",
      "preview_source": "indexed",
      "preview_truncated": false,
      "preview_available": true
    }
  ],
  "preview_coverage": {"covered": 18, "total": 20, "percentage": 90.0},
  "index_age": {"iso": "2026-02-23T10:30:00", "relative": "2 minutes ago"},
  "note": "Previews are first ~5000 chars only (not full content). Use read-email for complete content.",
  "background_indexing": {"status": "running", "remaining": 2, "check_command": "..."}
}
```

Per-message preview fields:
- `preview`: first 5000 chars of body, newlines replaced with spaces. Empty string if unavailable.
- `preview_source`: `"indexed"` | `"not_indexed"` | `"background_indexing"`
- `preview_truncated`: true if original content > 5000 chars
- `preview_available`: explicit boolean for agent branching

## list-emails

| Param               | Required | Default | Description                    |
| ------------------- | -------- | ------- | ------------------------------ |
| `--account EMAIL`   | yes      | —       | Account email address          |
| `--folder NAME`     | yes      | —       | Folder name (case-insensitive) |
| `--limit N`         | no       | 50      | Max emails                     |
| `--include-content` | no       | false   | Add preview from search index  |

Same return shape as `list-recent`.

## list-drafts

| Param               | Required | Default | Description                   |
| ------------------- | -------- | ------- | ----------------------------- |
| `--limit N`         | no       | 50      | Max drafts                    |
| `--include-content` | no       | false   | Add preview from search index |

Same return shape as `list-recent`.

## read-email

| Param     | Required | Description        |
| --------- | -------- | ------------------ |
| `--id ID` | yes      | Email ID (numeric) |

Returns full email:

```json
{
  "id": "1234", "subject": "...", "content": "full body...",
  "content_source": "jxa",
  "sender": "...", "sender_name": "...",
  "date_received": "...", "date_sent": "...",
  "read_status": true, "flagged_status": false,
  "account_email": "...", "folder_name": "...",
  "to_recipients": ["a@b.com"], "cc_recipients": [], "bcc_recipients": [],
  "attachments": [{"name": "file.pdf", "size": "12345"}]
}
```

Content retrieval uses a two-phase approach for resilience:
- **Phase 1** (always fast): metadata, recipients, attachments via JXA (~0.5 s)
- **Phase 2** (cascading fallback): JXA `content()` (10 s cap) → search index → disk `.emlx`

`content_source` values: `"jxa"` | `"search_index"` | `"disk"` | `"unavailable"`.
When `"unavailable"`, `content_note` explains why (large HTML, Exchange sync stall, etc.).

On stale ID: `error.code = "EMAIL_NOT_FOUND"` with `error.details.recovery` containing a relist command.

After successful fetch, content is automatically cached in the search index (cache-on-read).

## search

| Param             | Required | Default | Description                                       |
| ----------------- | -------- | ------- | ------------------------------------------------- |
| `--query TEXT`    | yes      | —       | Search query (FTS5 syntax for scope=all)          |
| `--scope`         | no       | `all`   | `all` (FTS5), `subject` (JXA), `sender` (JXA)     |
| `--account EMAIL` | no       | —       | Limit to account (warning if used with scope=all) |
| `--limit N`       | no       | 20      | Max results                                       |

Returns list of result dicts with `id`, `subject`, `sender`, `date_received`, `snippet`, `score`.

## compose-draft

| Param                   | Required | Description          |
| ----------------------- | -------- | -------------------- |
| `--account EMAIL`       | yes      | Sending account      |
| `--subject TEXT`        | yes      | Subject line         |
| `--body TEXT`           | yes      | Email body           |
| `--to ADDR...`          | yes      | Recipient(s)         |
| `--cc ADDR...`          | no       | CC recipient(s)      |
| `--bcc ADDR...`         | no       | BCC recipient(s)     |
| `--attachments PATH...` | no       | File paths to attach |

Returns `{success, message}`. Always re-list drafts to get the stable draft ID.

## amend-draft

| Param                   | Required | Description            |
| ----------------------- | -------- | ---------------------- |
| `--id ID`               | yes      | Draft ID               |
| `--subject TEXT`        | no       | New subject            |
| `--body TEXT`           | no       | New body               |
| `--cc ADDR...`          | no       | Replace CC list        |
| `--bcc ADDR...`         | no       | Replace BCC list       |
| `--attachments PATH...` | no       | Additional attachments |

Only provided fields are changed.

## send-draft

| Param     | Required | Description |
| --------- | -------- | ----------- |
| `--id ID` | yes      | Draft ID    |

## reply-draft

| Param                   | Required | Description                     |
| ----------------------- | -------- | ------------------------------- |
| `--id ID`               | yes      | Original email ID               |
| `--body TEXT`           | yes      | Reply body                      |
| `--reply-all`           | no       | Include all original recipients |
| `--cc ADDR...`          | no       | Additional CC                   |
| `--bcc ADDR...`         | no       | Additional BCC                  |
| `--attachments PATH...` | no       | File paths to attach            |

## forward-draft

| Param                   | Required | Description          |
| ----------------------- | -------- | -------------------- |
| `--id ID`               | yes      | Original email ID    |
| `--account EMAIL`       | yes      | Sending account      |
| `--body TEXT`           | yes      | Forward message body |
| `--to ADDR...`          | yes      | Recipient(s)         |
| `--cc ADDR...`          | no       | CC recipient(s)      |
| `--bcc ADDR...`         | no       | BCC recipient(s)     |
| `--attachments PATH...` | no       | File paths to attach |

## delete-email

| Param         | Required | Description                            |
| ------------- | -------- | -------------------------------------- |
| `--ids ID...` | yes      | Email ID(s) — supports single or batch |

Single ID returns `{success, message}`. Multiple IDs returns `{success, deleted, requested, not_found, message}`.

## delete-draft

| Param     | Required | Description |
| --------- | -------- | ----------- |
| `--id ID` | yes      | Draft ID    |

## move-email

| Param         | Required | Description                                                           |
| ------------- | -------- | --------------------------------------------------------------------- |
| `--id ID`     | yes      | Email ID                                                              |
| `--to FOLDER` | yes      | folder leaf name (BFS) or full path from `list-folders` `folder_path` |

Exchange accounts need ~3 s sync. full paths avoid ambiguity with ghost folders.

## amend-subject

| Param            | Required | Description                               |
| ---------------- | -------- | ----------------------------------------- |
| `--id ID`        | yes      | Email ID (numeric)                        |
| `--subject TEXT` | yes      | New subject line                          |
| `--dry-run`      | no       | Preview what would change without editing |

Edits the `.emlx` file on disk to change the subject of any email (received or draft).
Requires Full Disk Access. Quits and relaunches Mail.app to pick up the change (~5-8 s total).

**Dry-run response:**

```json
{
  "dry_run": true,
  "email_id": "4331",
  "original_subject": "Old Subject",
  "new_subject": "New Subject",
  "sender": "someone@example.com",
  "folder": "Inbox",
  "audit_log": ".../assets/amendment-log.jsonl"
}
```

**Success response:**

```json
{
  "email_id": "4331",
  "original_subject": "Old Subject",
  "new_subject": "New Subject",
  "sender": "someone@example.com",
  "folder": "Inbox",
  "audit_log": ".../assets/amendment-log.jsonl"
}
```

**Limitations:**
- IMAP/Exchange: server sync or mailbox rebuild may revert the change
- Local/POP: change is permanent
- Any open compose windows in Mail.app will be lost on quit
- Integer IDs may shift after Mail relaunch — re-list to verify
- Requires Full Disk Access (same as `build-index`)

**Error codes:** `EMAIL_NOT_FOUND`, `PERMISSION_DENIED`, `EMLX_REWRITE_FAILED`, `MAIL_QUIT_FAILED`

## add-label

| Param          | Required | Description                                    |
| -------------- | -------- | ---------------------------------------------- |
| `--id ID`      | yes      | Email ID (numeric)                             |
| `--label TEXT` | yes      | Label text (wrapped in brackets automatically) |
| `--dry-run`    | no       | Preview what would change without editing      |

Prepends `[label]` to the email's subject. Delegates to `amend-subject` internally.
If the subject already starts with `[label] `, returns success with no change (idempotent).

**Success response:**

```json
{
  "email_id": "4331",
  "original_subject": "Weekly Report",
  "new_subject": "[urgent] Weekly Report",
  "label": "urgent",
  "audit_log": ".../assets/amendment-log.jsonl"
}
```

**Already-labelled response:**

```json
{
  "email_id": "4331",
  "original_subject": "[urgent] Weekly Report",
  "new_subject": "[urgent] Weekly Report",
  "label": "urgent",
  "message": "email already has [urgent] label — no change needed"
}
```

Same caveats, error codes, and FDA requirement as `amend-subject`.

## build-index

No parameters. Full FTS5 rebuild from disk. Requires Full Disk Access. Returns `{indexed, mailboxes, elapsed_seconds, db_size_mb}`.

## index-status

No parameters. Returns background indexing state:

```json
{"status": "running|done|failed|cancelled|stale|not_running", "total": 18, "completed": 12, "percentage": 66.7}
```

## index-cancel

No parameters. Sends SIGTERM/SIGKILL to background worker, marks status as cancelled.

---

## Error Codes

| Code                   | When                                                      |
| ---------------------- | --------------------------------------------------------- |
| `EMAIL_NOT_FOUND`      | read/delete/move/amend-subject/add-label with stale ID    |
| `DRAFT_NOT_FOUND`      | draft operation with stale ID                             |
| `FOLDER_NOT_FOUND`     | move to nonexistent folder                                |
| `ACCOUNT_NOT_FOUND`    | operation with unknown account                            |
| `INVALID_ID`           | non-numeric ID                                            |
| `JXA_TIMEOUT`          | Mail.app unresponsive                                     |
| `PERMISSION_DENIED`    | Full Disk Access not granted (amend-subject, build-index) |
| `EMLX_REWRITE_FAILED`  | .emlx file edit failed (malformed file, write error)      |
| `MAIL_QUIT_FAILED`     | Mail.app could not be quit (amend-subject)                |
| `LOCK_TIMEOUT`         | index lock contention                                     |
| `PROGRESS_CORRUPTED`   | index-progress.json unreadable                            |
| `DISK_FULL`            | write failed due to disk space                            |
| `MICROMAMBA_NOT_FOUND` | launcher bootstrap failure                                |
| `INTERNAL_ERROR`       | unhandled exception                                       |
