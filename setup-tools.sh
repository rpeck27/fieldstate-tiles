#!/usr/bin/env bash
# Install the tile tools on an Ubuntu runner: tippecanoe (BSD-2-Clause, built
# from a pinned tag), go-pmtiles CLI (BSD-3-Clause, release binary), rclone.
# A pinned version that no longer downloads falls back to the latest release;
# the versions used are printed and written to $TOOLS_LOG.
set -euo pipefail
TIPPECANOE_REF="${TIPPECANOE_REF:-2.53.0}"
PMTILES_VERSION="${PMTILES_VERSION:-1.22.3}"
TOOLS_LOG="${TOOLS_LOG:-/tmp/tile-tools.txt}"

sudo apt-get update -qq
sudo apt-get install -y -qq build-essential libsqlite3-dev zlib1g-dev unzip rclone >/dev/null

latest() { curl -fsSL "https://api.github.com/repos/$1/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])'; }

if ! command -v tippecanoe >/dev/null; then
  src=/tmp/tippecanoe
  git clone -q --depth 1 --branch "$TIPPECANOE_REF" https://github.com/felt/tippecanoe.git "$src" \
    || { TIPPECANOE_REF="$(latest felt/tippecanoe)"; git clone -q --depth 1 --branch "$TIPPECANOE_REF" https://github.com/felt/tippecanoe.git "$src"; }
  make -s -C "$src" -j"$(nproc)" && sudo make -s -C "$src" install
fi

if ! command -v pmtiles >/dev/null; then
  arch="$(uname -m)"; case "$arch" in x86_64) a=x86_64;; aarch64|arm64) a=arm64;; esac
  get() { curl -fsSL "https://github.com/protomaps/go-pmtiles/releases/download/v$1/go-pmtiles_$1_Linux_${a}.tar.gz"; }
  if ! get "$PMTILES_VERSION" > /tmp/pmtiles.tgz; then
    PMTILES_VERSION="$(latest protomaps/go-pmtiles)"; PMTILES_VERSION="${PMTILES_VERSION#v}"
    get "$PMTILES_VERSION" > /tmp/pmtiles.tgz
  fi
  sudo tar -xzf /tmp/pmtiles.tgz -C /usr/local/bin pmtiles
fi

{ echo "tippecanoe=$(tippecanoe --version 2>&1 | head -1)"; echo "pmtiles=$(pmtiles version 2>&1 | head -1)"; echo "rclone=$(rclone version | head -1)"; } | tee "$TOOLS_LOG"
