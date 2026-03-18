#!/usr/bin/env bash
# Rebuild the FTS5 search index from scratch.
# Routes through mail.sh for micromamba bootstrap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIL_SH="$SCRIPT_DIR/mail.sh"

echo "Rebuilding search index (this may take a few minutes)..." >&2
exec "$MAIL_SH" build-index "$@"
