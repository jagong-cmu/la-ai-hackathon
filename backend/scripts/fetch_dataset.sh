#!/usr/bin/env bash
# Download and extract the Intuit QuickBooks hackathon dataset (26.7 MB -> ~403 MB).
# Then set QB_DATA_DIR in .env to the printed path.
set -euo pipefail
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/var/qbdata}"
mkdir -p "$DEST"
URL="https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026/resolve/main/hackathon-100-participant.tar.gz"
curl -fL --retry 3 -o "$DEST/dataset.tgz" "$URL"
tar -xzf "$DEST/dataset.tgz" -C "$DEST"
rm "$DEST/dataset.tgz"
echo "QB_DATA_DIR=$DEST/hackathon-100"
