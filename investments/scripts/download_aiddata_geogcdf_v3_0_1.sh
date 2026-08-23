#!/usr/bin/env bash
set -euo pipefail
source scripts/raw_utils.sh

SOURCE_ID="aiddata_china_geogcdf_v3_0_1"
VERSION="v3.0.1"
RELEASE_API="https://api.github.com/repos/aiddata/gcdf-geospatial-data/releases/tags/v3.0.1"
REPO_URL="https://github.com/aiddata/gcdf-geospatial-data"
OUTDIR="raw/investments/canonical_china/${SOURCE_ID}_2024_06_11"

mkdir -p "$OUTDIR/release_assets" "$OUTDIR/repo_snapshot"

echo "Fetching GitHub release metadata..."
curl -L --fail --retry 3 --retry-delay 5 \
  -o "$OUTDIR/github_release_v3_0_1.json" \
  "$RELEASE_API"

write_source_metadata \
  "$OUTDIR/source_metadata.json" \
  "$SOURCE_ID" \
  "AidData Geospatial Global Chinese Development Finance Dataset" \
  "$VERSION" \
  "$REPO_URL/releases/tag/v3.0.1"

echo "Listing release assets..."
jq -r '.assets[] | [.name, .browser_download_url] | @tsv' \
  "$OUTDIR/github_release_v3_0_1.json" \
  | tee "$OUTDIR/release_assets.tsv"

echo "Downloading release assets..."
while IFS=$'\t' read -r name url; do
  echo "Downloading $name"
  curl -L --fail --retry 3 --retry-delay 5 \
    -o "$OUTDIR/release_assets/$name" \
    "$url"
  checksum_file "$OUTDIR/release_assets/$name"
done < "$OUTDIR/release_assets.tsv"

echo "Also saving repo README/CHANGES/LICENSE raw copies..."
curl -L --fail -o "$OUTDIR/repo_snapshot/README.md" \
  "https://raw.githubusercontent.com/aiddata/gcdf-geospatial-data/main/README.md"
curl -L --fail -o "$OUTDIR/repo_snapshot/CHANGES.md" \
  "https://raw.githubusercontent.com/aiddata/gcdf-geospatial-data/main/CHANGES.md"
curl -L --fail -o "$OUTDIR/repo_snapshot/LICENSE.md" \
  "https://raw.githubusercontent.com/aiddata/gcdf-geospatial-data/main/LICENSE.md"

find "$OUTDIR" -maxdepth 4 -type f | sort > "$OUTDIR/file_inventory.txt"

echo "Done: $OUTDIR"
