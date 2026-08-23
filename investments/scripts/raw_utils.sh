#!/usr/bin/env bash
set -euo pipefail

checksum_file() {
  local file="$1"
  sha256sum "$file" | tee -a raw/_manifest/checksums_sha256.txt
}

write_source_metadata() {
  local out="$1"
  local source_id="$2"
  local source_name="$3"
  local version="$4"
  local url="$5"
  local accessed_date
  accessed_date="$(date -I)"

  cat > "$out" <<META
{
  "source_id": "$source_id",
  "source_name": "$source_name",
  "version": "$version",
  "url": "$url",
  "accessed_date": "$accessed_date",
  "raw_policy": "downloaded source artifact; do not edit in raw"
}
META
}
