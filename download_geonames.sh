#!/usr/bin/env bash
# Downloads the "GeoNames - all cities with a population > 1000" CSV used by
# update_tags_ui.py for reverse-geocoding photo GPS coordinates to city/country.
set -euo pipefail

OUT="${1:-geonames-all-cities-with-a-population-1000.csv}"
URL="https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/geonames-all-cities-with-a-population-1000/exports/csv?lang=en&timezone=UTC&use_labels=true&delimiter=%3B"

echo "Downloading GeoNames cities dataset to ${OUT}..."
curl -fL "$URL" -o "$OUT"
echo "Done: $(du -h "$OUT" | cut -f1) saved to ${OUT}"
echo ""
echo "If this download fails, get the CSV manually from:"
echo "https://public.opendatasoft.com/explore/dataset/geonames-all-cities-with-a-population-1000/export/"
echo "(use the ';'-delimited CSV export) and save it as ${OUT}."
