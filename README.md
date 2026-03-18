# apple-mail

a [Cursor](https://cursor.com) skill that gives your AI agent full control over macOS Mail.app -- read, search, compose, reply, forward, move, delete, and label emails, all from the editor.

drop this into `.cursor/skills/` and your agent can triage your inbox, draft replies, and organise mail without you leaving your IDE.

## requirements

- macOS with Mail.app configured
- [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) (env auto-created on first run)
- full disk access for Terminal (System Settings -> Privacy & Security -> Full Disk Access)

## setup

```bash
# copy into your project
cp -r apple-mail /path/to/project/.cursor/skills/

# verify setup
.cursor/skills/apple-mail/scripts/check-setup.sh
```

the skill auto-bootstraps a `mcp` micromamba environment with python 3.11 and installs dependencies on first use.

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

- reads email content from `.emlx` files on disk for speed (~5ms vs seconds via scripting bridge)
- falls back to JXA (JavaScript for Automation) when disk access misses
- maintains a SQLite FTS5 search index for fast body/subject/sender search
- background indexer runs automatically to keep the index warm
- all destructive operations (delete, amend-subject) log actions and support `--dry-run`

## safety

- compose and reply always create drafts first -- nothing sends without explicit confirmation
- delete requires user confirmation; `--dry-run` previews what would be removed
- amend-subject quits and relaunches Mail.app (warns about unsaved compose windows)

## project structure

```
scripts/
  mail.sh              # entrypoint -- bootstraps env, runs mail.py
  mail.py              # CLI dispatcher
  check-setup.sh       # verify prerequisites
  requirements.txt     # python dependencies (beautifulsoup4)
  lib/
    applescript.py      # AppleScript execution utilities
    jxa.py              # JXA execution + content enrichment pipeline
    mail_core.js        # shared JXA library for Mail.app
    ops/                # one module per command group
    search_index/       # FTS5 index: schema, disk reader, manager
references/
  tool-reference.md     # detailed parameter docs for each command
  writing-style.md      # email composition guidelines
SKILL.md                # Cursor skill descriptor (agent instructions)
```

## acknowledgements

the disk-first reading approach and FTS5 search index design were inspired by [imdinu/jxa-mail-mcp](https://github.com/imdinu/jxa-mail-mcp).

## license

MIT
