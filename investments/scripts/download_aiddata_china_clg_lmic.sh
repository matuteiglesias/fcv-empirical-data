#!/usr/bin/env bash
set -euo pipefail
source scripts/raw_utils.sh

SOURCE_ID="aiddata_china_clg_lmic_v1_0"
VERSION="v1.0"
URL="https://docs.aiddata.org/ad4/datasets/AidDatas_CLG_LMIC_Dataset_v1.0.zip"
OUTDIR="raw/investments/canonical_china/${SOURCE_ID}_2025_11_18"
ZIP="${OUTDIR}/AidDatas_CLG_LMIC_Dataset_v1.0.zip"

mkdir -p "$OUTDIR/extracted"

echo "Downloading AidData CLG-LMIC v1.0..."
curl -L --fail --retry 3 --retry-delay 5 \
  -o "$ZIP" \
  "$URL"

checksum_file "$ZIP"

write_source_metadata \
  "$OUTDIR/source_metadata.json" \
  "$SOURCE_ID" \
  "AidData China's Loans and Grants to Low- and Middle-Income Countries Dataset" \
  "$VERSION" \
  "$URL"

unzip -n "$ZIP" -d "$OUTDIR/extracted" | tee "logs/${SOURCE_ID}_unzip.log"

find "$OUTDIR" -maxdepth 3 -type f | sort > "$OUTDIR/file_inventory.txt"

echo "Done: $OUTDIR"
