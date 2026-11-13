#!/usr/bin/env bash
# Run the Malitan container in the background, replacing any previous one.
# Usage:
#   scripts/docker-run.sh                  start the app on http://localhost:5001
#   scripts/docker-run.sh flask init-db    run a one-off command against the same volume
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"
IMAGE="${IMAGE:-malitan:dev}"
NAME="malitan"
VOLUME="malitan-data"
PORT="${PORT:-5001}"
ENV_FILE="$APP_DIR/.env"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE (copy .env.example)" >&2; exit 1; }

if [[ $# -gt 0 ]]; then
  # One-off command: throwaway container, same image, env and volume.
  exec docker run --rm -it --env-file "$ENV_FILE" -v "$VOLUME:/data" "$IMAGE" "$@"
fi

# A stopped or running container with the same name would make docker run fail.
docker rm -f "$NAME" >/dev/null 2>&1 || true

docker run -d \
  --name "$NAME" \
  --env-file "$ENV_FILE" \
  -v "$VOLUME:/data" \
  -p "$PORT:5001" \
  "$IMAGE"

echo "Started $NAME on http://localhost:$PORT  (logs: docker logs -f $NAME)"
