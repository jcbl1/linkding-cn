#!/usr/bin/env bash
# 从本地安装的 single-file-cli 和 defuddle 提取参数列表。
# 直接读本地文件，不依赖网络、不需要版本检测。
#
# 用法: ./scripts/generate-site-adapters-etc.sh
# 输出:
#   bookmarks/services/site_adapters/etc/singlefile_args.txt
#   bookmarks/services/site_adapters/etc/defuddle_params.txt

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/bookmarks/services/site_adapters/etc"
mkdir -p "$OUT_DIR"

# SingleFile: 从全局 npm 包读 options.js
SF_BIN="$(command -v single-file 2>/dev/null)" || { echo "错误: single-file 未安装" >&2; exit 1; }
SF_OPTIONS="$(cd "$(dirname "$SF_BIN")/.." && pwd)/lib/node_modules/single-file-cli/options.js"
if [ ! -f "$SF_OPTIONS" ]; then
  echo "错误: $SF_OPTIONS 不存在" >&2; exit 1
fi

# Defuddle: 从 node_modules 读类型定义
DEF_TYPES="$REPO_ROOT/node_modules/defuddle/dist/types.d.ts"
if [ ! -f "$DEF_TYPES" ]; then
  echo "错误: $DEF_TYPES 不存在，请先 npm install" >&2; exit 1
fi

grep -oE '"[a-z][-a-z]*":' "$SF_OPTIONS" | sed 's/[":]//g; s/^/--/' | sort -u > "$OUT_DIR/singlefile_args.txt"
awk '/interface DefuddleOptions/,/^}/' "$DEF_TYPES" | grep -oE '^\s+[a-z][a-zA-Z]*\?' | sed 's/?//' | awk '{print $1}' | sort -u > "$OUT_DIR/defuddle_params.txt"

echo "SingleFile: $(wc -l < "$OUT_DIR/singlefile_args.txt" | tr -d ' ') args"
echo "Defuddle: $(wc -l < "$OUT_DIR/defuddle_params.txt" | tr -d ' ') params"
