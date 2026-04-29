#!/usr/bin/env bash
# Regenerate Python protobuf bindings from the .proto files in this dir.
# Output goes into bin/targets/_proto/, which targets/saved_tab_groups.py adds
# to sys.path at import time so the generated files can import each other
# (they use absolute imports, the way protoc emits them).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../bin/targets/_proto"

if ! command -v protoc >/dev/null 2>&1; then
    echo "error: protoc not found. Install with: brew install protobuf" >&2
    exit 1
fi

mkdir -p "$OUT"
cd "$HERE"
protoc --proto_path=. --python_out="$OUT" \
    saved_tab_group_specifics.proto \
    tab_group_attribution_metadata.proto \
    local_entity_wrapper.proto

echo "Generated in $OUT:"
ls -1 "$OUT"
