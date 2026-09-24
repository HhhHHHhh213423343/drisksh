#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR=${1:-"$PROJECT_DIR/zeabur-direct-deploy"}

mkdir -p "$OUTPUT_DIR/backend" "$OUTPUT_DIR/frontend"

rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$PROJECT_DIR/backend/app/" "$OUTPUT_DIR/backend/app/"
cp "$PROJECT_DIR/backend/requirements.txt" "$OUTPUT_DIR/backend/requirements.txt"

rsync -a --delete \
  --exclude 'node_modules/' \
  --exclude '.next*/' \
  --exclude '.DS_Store' \
  --exclude 'tsconfig.tsbuildinfo' \
  "$PROJECT_DIR/frontend/" "$OUTPUT_DIR/frontend/"

for required in Dockerfile start.sh .dockerignore .env.example README-部署.md; do
  if [ ! -f "$OUTPUT_DIR/$required" ]; then
    echo "部署包缺少 $required，请从已交付的 zeabur-direct-deploy 目录恢复后重试。" >&2
    exit 1
  fi
done

echo "Zeabur 直传部署包已刷新：$OUTPUT_DIR"
