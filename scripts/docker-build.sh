#!/usr/bin/env bash
# Build the Malitan image.
set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") [--tag IMAGE] [--platform PLATFORMS]

  -t, --tag IMAGE           image name and tag (default: malitan:dev)
  -p, --platform PLATFORMS  comma-separated list (default: linux/amd64)
                            e.g. linux/amd64,linux/arm64 (arm64 runs under QEMU: slow)
  -h, --help                show this help
EOF
}

IMAGE="malitan:dev"
PLATFORM="linux/amd64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--tag)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; usage >&2; exit 2; }
      IMAGE="$2"; shift 2 ;;
    -p|--platform)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; usage >&2; exit 2; }
      PLATFORM="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"
PYTHON_VERSION="$(cat "$APP_DIR/.python-version")"

docker build \
  --platform "$PLATFORM" \
  --build-arg PYTHON_VERSION="$PYTHON_VERSION" \
  -t "$IMAGE" \
  "$APP_DIR"

echo "Built $IMAGE for $PLATFORM (Python $PYTHON_VERSION)"
