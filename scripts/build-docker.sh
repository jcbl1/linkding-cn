#!/usr/bin/env bash
set -euo pipefail

version=$(<version.txt)

# Default to TUNA mirror for faster downloads in China.
# For overseas builds where TUNA is slower, use --no-mirror.
# Usage:
#   ./scripts/build-docker.sh              # default (Debian), TUNA mirror
#   ./scripts/build-docker.sh --alpine     # Alpine variant, TUNA mirror
#   ./scripts/build-docker.sh --no-mirror  # default (Debian), official source
#   ./scripts/build-docker.sh --alpine --no-mirror  # Alpine, official source
MIRROR_ARG="--build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn"
DOCKERFILE="docker/default.Dockerfile"

if [[ "${1:-}" == "--alpine" ]]; then
    DOCKERFILE="docker/alpine.Dockerfile"
    MIRROR_ARG="--build-arg APK_MIRROR=mirrors.tuna.tsinghua.edu.cn"
    shift
fi

if [[ "${1:-}" == "--no-mirror" ]]; then
    MIRROR_ARG=""
fi

# Plus image with support for single-file snapshots
# Needs checking if this works with ARMv7, excluded for now
docker buildx build --target linkding-plus --platform linux/amd64,linux/arm64 \
  -f "$DOCKERFILE" \
  -t woohoodai/linkding-cn:latest \
  -t woohoodai/linkding-cn:v$version \
  $MIRROR_ARG \
  --push .
