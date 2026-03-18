# apple-mail

an agent skill that gives AI coding assistants full control over macOS Mail.app. read, search, compose, reply, forward, move, delete, and label emails without leaving the editor.

place this in your agent's skill directory (e.g. `.cursor/skills/`) and it can triage your inbox, draft replies, and organise mail on your behalf.

## requirements

- macOS with Mail.app configured
- [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) (env auto-created on first run)
- full disk access for Terminal (System Settings, Privacy & Security, Full Disk Access)

## setup

```bash
cp -r apple-mail /path/to/project/.cursor/skills/

# verify prerequisites
.cursor/skills/apple-mail/scripts/check-setup.sh
```

on first use, `mail.sh` creates a `mcp` micromamba environment with python 3.11 and installs dependencies automatically.

## commands

all commands go through `scripts/mail.sh` and return structured JSON.

| command                                                              | what it does                                     |
| -------------------------------------------------------------------- | ------------------------------------------------ |
| `list-accounts`                                                      | list all mail accounts                           |
| `list-folders --account EMAIL`                                       | folder tree with email counts                    |
| `list-recent [--limit N] [--include-content]`                        | recent inbox emails                              |
| `list-emails --account EMAIL --folder NAME [--limit N]`              | emails in a specific folder                      |
| `list-drafts [--limit N]`                                            | drafts across all accounts                       |
| `read-email --id ID`                                                 | full email content, recipients, attachments      |
| `search --query TEXT [--scope SCOPE] [--limit N]`                    | search emails (scope: all, subject, sender)      |
| `compose-draft --account EMAIL --subject TEXT --body TEXT --to ADDR` | create a new draft                               |
| `amend-draft --id ID [--subject TEXT] [--body TEXT]`                 | modify an existing draft                         |
| `send-draft --id ID`                                                 | send a draft                                     |
| `reply-draft --id ID --body TEXT [--reply-all]`                      | create a reply draft                             |
| `forward-draft --id ID --account EMAIL --body TEXT --to ADDR`        | forward as draft                                 |
| `delete-email --ids ID [ID...] [--dry-run]`                          | delete emails (exchange-safe via RFC Message-ID) |
| `delete-draft --id ID`                                               | delete a draft                                   |
| `move-email --id ID --to FOLDER`                                     | move email to folder                             |
| `amend-subject --id ID --subject TEXT [--dry-run]`                   | edit subject line on disk                        |
| `add-label --id ID --label TEXT [--dry-run]`                         | prepend [label] to subject                       |
| `build-index`                                                        | build/rebuild FTS5 search index                  |

## how it works

- reads email content from `.emlx` files on disk (~5 ms per message, compared to seconds via the scripting bridge)
- falls back to JXA (JavaScript for Automation) when disk access misses
- maintains a SQLite FTS5 search index for fast body, subject, and sender search
- a background indexer runs automatically to keep the index current
- all destructive operations (delete, amend-subject) log their actions and support `--dry-run`

## safety

- compose and reply always create drafts first; nothing sends without explicit confirmation
- delete requires user confirmation, and `--dry-run` previews what would be removed
- amend-subject quits and relaunches Mail.app (the agent warns about unsaved compose windows)

## project structure

```
scripts/
  mail.sh              # entrypoint: bootstraps env, runs mail.py
  mail.py              # CLI dispatcher
  check-setup.sh       # verify prerequisites
  requirements.txt     # python dependencies (beautifulsoup4)
  lib/
    applescript.py      # AppleScript execution utilities
    jxa.py              # JXA execution and content enrichment
    mail_core.js        # shared JXA library for Mail.app
    ops/                # one module per command group
    search_index/       # FTS5 index: schema, disk reader, manager
references/
  tool-reference.md     # detailed parameter docs for each command
SKILL.md                # skill descriptor (agent instructions)
```

## acknowledgements

the disk-first reading approach and FTS5 search index design were inspired by [imdinu/jxa-mail-mcp](https://github.com/imdinu/jxa-mail-mcp).

## license

MIT
