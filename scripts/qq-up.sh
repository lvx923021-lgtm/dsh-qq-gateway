#!/usr/bin/env bash
# 把 QQ/NapCat 拉起来：优先快速登录；真登不上就自动把二维码递到 hd 的 Download。
# 用法： bash qq-up.sh
set -u

UIN=${BOT_UIN:-}
NAP=/opt/QQ
LOG=/root/napcat/qq-up.log
QR_SRC=/opt/QQ/resources/app/napcat/cache/qrcode.png
QR_DST=/root/Documents/deepseek-harness/default-workspace/qq-login-qr.png
BRIDGE=/root/.dsh/.bridge_headers

mkdir -p /root/napcat

onebot_ok() {
  python3 - <<'PY'
import json, sys, urllib.request
try:
    req = urllib.request.Request(
        "http://127.0.0.1:3000/get_login_info",
        data=b"{}", headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=5))
    sys.exit(0 if d.get("retcode") == 0 else 1)
except Exception:
    sys.exit(1)
PY
}

if onebot_ok; then
  echo "OneBot 已在线，不用动"
  exit 0
fi

echo "拉起 QQ（快速登录 -q $UIN）"
pkill -x qq 2>/dev/null
pkill -x Xvfb 2>/dev/null
sleep 3
( cd "$NAP" && nohup xvfb-run -a ./qq --no-sandbox --disable-dev-shm-usage -q "$UIN" >>"$LOG" 2>&1 & )

for i in $(seq 1 24); do
  sleep 5
  if onebot_ok; then
    echo "登录成功，OneBot 就绪"
    exit 0
  fi
  if grep -q "请扫描下面的二维码" "$LOG" 2>/dev/null; then
    sleep 2
    if [ -f "$QR_SRC" ]; then
      cp -f "$QR_SRC" "$QR_DST" 2>/dev/null && echo "快速登录失败，需要扫码；二维码已备好"
      curl -sS -m 20 -H "@$BRIDGE" \
        "http://127.0.0.1:3090/app/export?path=$QR_DST" >/dev/null 2>&1 && echo "已推送到 Download"
      curl -sS -m 20 -H "@$BRIDGE" \
        "http://127.0.0.1:3090/app/notify?title=%E5%B0%8F%E9%B1%BC%E6%8E%89%E7%BA%BF%E4%BA%86&text=QQ%E9%9C%80%E8%A6%81%E6%89%AB%E7%A0%81%EF%BC%8C%E7%A0%81%E5%9C%A8%20Download/qq-login-qr.png" >/dev/null 2>&1
    fi
    exit 2
  fi
done

echo "等了 2 分钟还没登上来，看 $LOG"
exit 3
