#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BROWSER_CHOICE=${DRISK_BROWSER:-auto}
EDGE_APP="/Applications/Microsoft Edge.app"
CHROME_APP="/Applications/Google Chrome.app"
# Edge 和 Chrome 的登录数据加密方式不同，不能共用目录；登录、校准、采集须使用同一浏览器。
case "$BROWSER_CHOICE" in
  chrome) BROWSER_APP="$CHROME_APP" ;;
  edge) BROWSER_APP="$EDGE_APP" ;;
  auto)
    if [ -d "$EDGE_APP" ]; then BROWSER_APP="$EDGE_APP"; else BROWSER_APP="$CHROME_APP"; fi ;;
  *) echo "DRISK_BROWSER 只能是 edge、chrome 或 auto。" >&2; exit 1 ;;
esac
if [ ! -d "$BROWSER_APP" ]; then
  echo "未找到浏览器：$BROWSER_APP" >&2
  exit 1
fi
if [ "$BROWSER_APP" = "$CHROME_APP" ]; then
  DEFAULT_PROFILE="$PROJECT_DIR/.runtime/qyyjt-chrome"
  BROWSER_PATH="$CHROME_APP/Contents/MacOS/Google Chrome"
else
  DEFAULT_PROFILE="$PROJECT_DIR/.runtime/qyyjt-edge"
  BROWSER_PATH="$EDGE_APP/Contents/MacOS/Microsoft Edge"
fi
PROFILE_DIR=${DRISK_QYYJT_PROFILE_DIR:-"$DEFAULT_PROFILE"}
# 默认直连：企业预警通只接受中国大陆 IP，本机 VPN 仍供其他浏览器使用。
if [ "${DRISK_USE_SYSTEM_PROXY:-0}" = "1" ]; then
  DIRECT_FLAG=""
  BROWSER_PROXY_ARG=""
else
  DIRECT_FLAG="--direct-connection"
  BROWSER_PROXY_ARG="--no-proxy-server"
fi
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

PYTHON_BIN=${DRISK_WORKER_PYTHON:-python3}
# 采集程序通过本机端口控制浏览器，本机地址不能经过 HTTP_PROXY 转发。
NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
no_proxy="$NO_PROXY"
export NO_PROXY no_proxy
"$PYTHON_BIN" -c 'import DrissionPage, openpyxl' >/dev/null 2>&1 || {
  echo "当前 Python 缺少采集依赖，请先执行：python3 -m pip install -r requirements.txt" >&2
  exit 1
}

# 由脚本启动专用浏览器并开放本机调试端口，Python 只负责附着；
# 登录窗口仍开着时直接复用，不需要先退出。
DEBUG_PORT=${DRISK_DEBUG_PORT:-9555}
STARTED_BROWSER=""
browser_ready() {
  curl --noproxy '*' -s -m 2 "http://127.0.0.1:$DEBUG_PORT/json/version" >/dev/null 2>&1
}
if ! browser_ready; then
  "$BROWSER_PATH" \
    --remote-debugging-port="$DEBUG_PORT" \
    --user-data-dir="$PROFILE_DIR" \
    --profile-directory=Default \
    --no-first-run \
    --no-default-browser-check \
    $BROWSER_PROXY_ARG \
    about:blank >/dev/null 2>&1 &
  STARTED_BROWSER=$!
  trap 'if [ -n "$STARTED_BROWSER" ]; then kill "$STARTED_BROWSER" 2>/dev/null || true; fi' EXIT
  WAITED=0
  until browser_ready; do
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -gt 60 ]; then
      echo "专用浏览器 60 秒内未开放调试端口 $DEBUG_PORT，请确认该浏览器目录未被其他窗口占用。" >&2
      exit 1
    fi
    sleep 1
  done
fi

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -u -m enterprise_sentinel.company_profile.agent \
  --api-url "${API_URL%/}" \
  --user-data-dir "$PROFILE_DIR" \
  --profile-directory Default \
  --browser-path "$BROWSER_PATH" \
  --use-live-profile \
  --once \
  --attach-port "$DEBUG_PORT"
