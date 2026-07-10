#!/bin/bash
# Build work/ref/ref_streets.tif — TIGER street centerlines for
# Charlottesville city (51540) + a margin of Albemarle county (51003),
# rasterized onto the verification grid (EPSG:2284, 16 ft/px). This is
# the absolute georeferencing reference every edition is verified
# against (ported from arlington-glup make_ref.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p work/ref
cd work/ref

for fips in 51540 51003; do
  if [ ! -f "tl_2023_${fips}_roads.shp" ]; then
    curl -s -O "https://www2.census.gov/geo/tiger/TIGER2023/ROADS/tl_2023_${fips}_roads.zip"
    unzip -o -q "tl_2023_${fips}_roads.zip"
  fi
done
ogr2ogr -overwrite -t_srs EPSG:2284 roads2284.shp tl_2023_51540_roads.shp
ogr2ogr -append -t_srs EPSG:2284 roads2284.shp tl_2023_51003_roads.shp
# grid covers every edition's extent with margin (2003 sheet is largest)
gdal_rasterize -q -burn 255 -te 11470000 3880000 11504000 3922000 \
  -tr 16 16 -ot Byte roads2284.shp ref_streets.tif
echo "wrote work/ref/ref_streets.tif"
