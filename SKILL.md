---
name: apple-mail
description: read, write, search, and manage emails in macos mail.app via cli scripts. use when the user wants to send, read, search, draft, or organise emails.
disable-model-invocation: true
---

# apple mail skill

all commands: `.cursor/skills/apple-mail/scripts/mail.sh <command> [args]`

all output is json: `{success, data, error, warnings, meta}`

## commands

| command | what it does |
| --- | --- |
| `list-accounts` | list all mail accounts |
| `list-folders --account EMAIL` | folder tree with email counts ; output includes `folder_path` for use with `move-email` |
| `list-recent [--limit N] [--include-content]` | recent inbox emails ; always use `--include-content` for triage |
| `list-emails --account EMAIL --folder NAME [--limit N] [--include-content]` | emails in a specific folder |
| `list-drafts [--limit N] [--include-content]` | drafts across all accounts |
| `read-email --id ID` | full email content, recipients, attachments |
| `search --query TEXT [--scope all\|subject\|sender] [--limit N]` | search emails |
| `compose-draft --account EMAIL --subject TEXT --body TEXT --to ADDR...` | create a new draft |
| `amend-draft --id ID [--subject TEXT] [--body TEXT]` | modify an existing draft |
| `send-draft --id ID` | send a draft |
| `reply-draft --id ID --body TEXT [--reply-all]` | create a reply draft |
| `forward-draft --id ID --account EMAIL --body TEXT --to ADDR...` | forward as draft |
| `delete-email --ids ID [ID...] [--dry-run] [--force]` | delete email(s) ; exchange-safe via RFC Message-ID |
| `delete-draft --id ID` | delete a draft |
| `move-email --id ID --to FOLDER` | move email ; accepts leaf name or full path (e.g. `--to "📂own-dirs/paperworks"`) |
| `move-to-todos --id ID` | shortcut for move to 📝todos |
| `amend-subject --id ID --subject TEXT [--dry-run]` | edit subject on disk ; quits+relaunches Mail.app |
| `add-label --id ID --label TEXT [--dry-run]` | prepend [label] to subject ; wraps amend-subject |
| `build-index` | build/rebuild search index (~30-120 s) |

array args use space separation: `--to a@b.com c@d.com --cc x@y.com`

for detailed parameter docs, see `references/tool-reference.md`.

## workflows

### triage inbox

1. `list-recent --include-content` ; always include content -- metadata-only has ~30-40% error rate
2. `read-email --id ID` ; mandatory for: unknown senders, non-English subjects, generic subjects, attachments, anything <90% confidence
3. `delete-email --ids ID1 ID2 ... --dry-run` ; verify resolved subjects match before deleting
4. `delete-email --ids ID1 ID2 ...` ; confirm with user first
5. `move-email --id ID --to Archive` ; or archive instead of deleting

tiered confirmation: batch confident garbage (CI, marketing, expired promos) ; list separately anything matching a high-risk category

### reply / compose / send

1. `read-email --id ID` then `reply-draft --id ID --body "..."` (or `compose-draft`)
2. `list-drafts` ; get stable draft id
3. show draft to user, wait for explicit confirmation
4. `send-draft --id DRAFT_ID`

### move to folder

`move-email --to` accepts both leaf names and full paths (use `folder_path` from `list-folders` output):
- `--to paperworks` ; finds first match at any depth
- `--to "📂own-dirs/paperworks"` ; walks path explicitly, no ambiguity ; prefer this

if folder does not exist: tell the user to create it in Outlook Web App (outlook.office.com), then `list-folders` to verify it synced, then move.

never create folders via AppleScript/osascript/JXA -- they create local-only ghosts that Exchange ignores. never attempt workarounds (exchangelib, msal, Graph API, EWS, PowerShell). the error messages from `move-email` include recovery instructions.

## safety rules

1. always draft first, show draft to user, wait for confirmation before sending
2. never delete without user confirmation ; use `--dry-run` first for batches
3. if any command returns "not found", re-list the folder for fresh IDs and retry (Exchange reassigns integer IDs on sync)
4. `amend-subject` / `add-label` quit and relaunch Mail.app -- warn user about unsaved compose windows ; always `--dry-run` first

## high-risk categories ; never auto-delete

always confirm with user or `read-email` before deleting:

- financial: bank transactions, payment confirmations, 2FA codes
- security: "new sign-in", "password changed", vulnerability alerts
- shipping for expensive items, legal/government, medical
- emails with attachments (the attachment IS the value)
- non-English emails (especially CJK)
- github security alerts (look identical to CI noise)
- empty/generic subjects: "(no subject)", "Re:", "FYI"
- unknown senders (could be a new collaborator, not cold outreach)

## writing style

see `references/writing-style.md` for email composition guidelines.
