#!/usr/bin/env bash
# Build and push sarumont/sxm-streamer:dev to Docker Hub.
# Usage: ./publish-dev.sh [--platform <platform>]
#   Default platform: linux/amd64
set -euo pipefail

IMAGE="sarumont/sxm-streamer"
TAG="dev"
PLATFORM="${PLATFORM:-linux/amd64}"

# Allow --platform override via flag
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Detect container runtime: prefer docker unless it's actually podman under the hood
if command -v docker &>/dev/null && ! docker --version 2>&1 | grep -qi podman; then
  RUNTIME="docker"
elif command -v podman &>/dev/null; then
  RUNTIME="podman"
else
  echo "Error: neither docker nor podman found in PATH" >&2
  exit 1
fi

echo "Building ${IMAGE}:${TAG} for ${PLATFORM} (runtime: ${RUNTIME})..."

if [[ "${RUNTIME}" == "docker" ]]; then
  docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${IMAGE}:${TAG}" \
    --push \
    .
else
  podman build \
    --platform "${PLATFORM}" \
    --tag "${IMAGE}:${TAG}" \
    .
  podman push "${IMAGE}:${TAG}"
fi

echo "Pushed ${IMAGE}:${TAG}"
