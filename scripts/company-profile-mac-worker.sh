#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PROFILE_DIR=${DRISK_QYYJT_PROFILE_DIR:-"$PROJECT_DIR/.runtime/qyyjt-edge"}
API_URL=${DRISK_API_URL:-${1:-}}

if [ -z "$API_URL" ]; then
  echo "请通过 DRISK_API_URL 或第一个参数提供 Zeabur 平台地址。" >&2
  exit 1
fi
if [ "${COLLECTION_API_KEY:-}" = "" ] || [ "${#COLLECTION_API_KEY}" -lt 32 ]; then
  echo "请在当前终端设置与 Zeabur 一致的 COLLECTION_API_KEY（建议至少32位）。" >&2
  exit 1
fi
if [ ! -d "$PROFILE_DIR/Default" ]; then
  echo "未找到专用登录会话，请先运行 scripts/company-profile-mac-login.sh。" >&2
  exit 1
fi

if [ -x "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]; then
  BROWSER_PATH="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
elif [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  BROWSER_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  echo "未找到可执行的 Edge 或 Chrome。" >&2
  exit 1
fi

PYTHON_BIN=${DRISK_WORKER_PYTHON:-python3}
"$PYTHON_BIN" -c 'import DrissionPage, openpyxl' >/dev/null 2>&1 || {
  echo "当前 Python 缺少采集依赖，请先执行：python3 -m pip install -r requirements.txt" >&2
  exit 1
}

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -u -m enterprise_sentinel.company_profile.agent \
  --api-url "${API_URL%/}" \
  --user-data-dir "$PROFILE_DIR" \
  --profile-directory Default \
  --browser-path "$BROWSER_PATH" \
  --use-live-profile \
  --once
