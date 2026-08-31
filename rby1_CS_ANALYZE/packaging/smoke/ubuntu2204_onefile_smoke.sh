#!/usr/bin/env bash
set -euo pipefail

launcher=${1:?"usage: $0 ONEFILE_EXECUTABLE"}

if [[ $(id -u) -eq 0 ]]; then
  echo "refusing privileged package smoke" >&2
  exit 2
fi
[[ -x "$launcher" ]] || { echo "onefile launcher is not executable: $launcher" >&2; exit 2; }

export http_proxy=http://127.0.0.1:9
export https_proxy=http://127.0.0.1:9
export all_proxy=http://127.0.0.1:9
export no_proxy=127.0.0.1,localhost,::1

"$launcher" --self-test

runtime_dir=$(mktemp -d)
trap 'rm -rf "$runtime_dir"' EXIT
chmod 700 "$runtime_dir"
export XDG_RUNTIME_DIR="$runtime_dir"
export RBY1_CS_ANALYZER_DATA_ROOT="$runtime_dir/data"

log="$runtime_dir/launcher.log"
"$launcher" --no-open-browser --port 0 >"$log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; rm -rf "$runtime_dir"' EXIT
for _ in $(seq 1 200); do
  grep -qE 'http://(127\.0\.0\.1|localhost):[0-9]+' "$log" && break
  kill -0 "$pid" 2>/dev/null || { cat "$log" >&2; exit 1; }
  sleep 0.1
done
grep -qE 'http://(127\.0\.0\.1|localhost):[0-9]+' "$log"
url=$(grep -oE 'http://(127\.0\.0\.1|localhost):[0-9]+' "$log" | sed -n '1p')

for asset in \
  /models/rby1a/urdf/model_v1.2.urdf \
  /models/rby1a/urdf/meshes/base.glb \
  /models/rby1m/urdf/model_v1.2.urdf \
  /models/rby1m/urdf/model_v1.3.urdf \
  /models/rby1m/urdf/meshes/LINK_11_WY.dae \
  /models/rby1m/urdf/meshes/base.glb; do
  output="$runtime_dir/$(basename "$asset")-$(printf '%s' "$asset" | cksum | cut -d' ' -f1)"
  curl --fail --silent --show-error --noproxy '*' "$url$asset" --output "$output"
  [[ -s "$output" ]] || { echo "empty packaged model asset: $asset" >&2; exit 1; }
done

kill "$pid"
wait "$pid" 2>/dev/null || true
echo "PASS: offline unprivileged onefile smoke"
