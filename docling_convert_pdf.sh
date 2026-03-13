#!/usr/bin/env bash
set -euo pipefail;

docling_dir="$(dirname "$(realpath "${BASH_SOURCE[0]}")")";

while [ -n "${1:-}" ]; do

  # Get the first file
  file_name="$1";
  target="$(realpath "${file_name}" 2>/dev/null || echo "${file_name}")";
  shift;

  # Ensure the file exists
  if ! [ -e "$target" ]; then
    echo "Skipping ${file_name}: doesn't exist.";
    continue;
  fi;

  # Ensure the file is a PDF
  if ! echo "${target}" | grep -q 'pdf$'; then
    echo "Skipping ${file_name}: not a PDF.";
    continue;
  fi;

  # Ensure the target path doesn't exist
  if [ -d "${target/.pdf/_docling}" ]; then
    echo "Skipping ${file_name}: already extracted.";
    continue;
  fi;

  ( cd "$docling_dir" && uv run python convert_pdf.py "$target" );

  cp "${target/.pdf/_docling}/output_pages.md" "${target/.pdf/.md}";

done;
