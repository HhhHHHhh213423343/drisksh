#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PROFILE_DIR=${DRISK_QYYJT_PROFILE_DIR:-"$PROJECT_DIR/.runtime/qyyjt-edge"}

if [ -d "/Applications/Microsoft Edge.app" ]; then
  BROWSER_APP="Microsoft Edge"
elif [ -d "/Applications/Google Chrome.app" ]; then
  BROWSER_APP="Google Chrome"
else
  echo "未找到 Microsoft Edge 或 Google Chrome。" >&2
  exit 1
fi

mkdir -p "$PROFILE_DIR"
open -na "$BROWSER_APP" --args \
  --user-data-dir="$PROFILE_DIR" \
  --profile-directory=Default \
  "https://www.qyyjt.cn/user/login"

echo "已打开企业预警通专用浏览器。"
echo "请完成登录并确认可打开“上海携程金融信息服务有限公司”详情页，然后关闭该浏览器窗口。"
echo "专用登录数据保存在已忽略的目录：$PROFILE_DIR"
