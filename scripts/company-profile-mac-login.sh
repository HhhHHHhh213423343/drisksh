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

mkdir -p "$PROFILE_DIR"
open -na "$BROWSER_APP" --args \
  --user-data-dir="$PROFILE_DIR" \
  --profile-directory=Default \
  --remote-debugging-port="${DRISK_DEBUG_PORT:-9555}" \
  $BROWSER_PROXY_ARG \
  "https://www.qyyjt.cn/user/login"

echo "已打开企业预警通专用浏览器。"
echo "请完成登录并确认可打开“上海携程金融信息服务有限公司”详情页。窗口可以保持打开，校准和采集脚本会直接复用。"
echo "专用登录数据保存在已忽略的目录：$PROFILE_DIR"
