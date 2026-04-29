#!/usr/bin/env bash
# Rebuild the leveldb-writer binary. Run from anywhere.
set -euo pipefail

if ! command -v go >/dev/null 2>&1; then
    echo "error: Go not installed. Install with: brew install go" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/leveldb_writer"
go build -trimpath -ldflags="-s -w" -o "$HERE/../leveldb-writer" .

echo "built: $HERE/../leveldb-writer"
file "$HERE/../leveldb-writer"
