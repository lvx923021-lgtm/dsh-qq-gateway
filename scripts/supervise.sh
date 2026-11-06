#!/usr/bin/env bash
# 互保层：只做一件事——保证看门狗活着。
# 看门狗再保证网关和 QQ 活着。两层互相兜底，任何一层被误杀都能自己回来。
set -u
DIR=/root/qq-gateway
LOG=$DIR/supervise.log
LOCK=$DIR/.supervise.pid

mkdir -p "$DIR"
LOCKFILE="$(dirname "$0")/.supervise.lock"
exec 9>"$LOCKFILE"
flock -w 10 9 || { echo "等了 10 秒仍有一份健康的在跑，退出"; exit 0; }
echo $$ > "$LOCK"
log(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }
log "互保层启动 pid=$$"

DSH_HOST_LOG=${QQ_HOME:-/root/qq-gateway}/../dsh-web.log
DSH_BIN=/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js
LAST_HOST_BOOT=/root/qq-gateway/.last_host_boot
HOST_FAILS=/root/qq-gateway/.host_fails

host_ok(){
  pgrep -f "dsh/lib/bin.js web" >/dev/null || return 1
  code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:3080/ 2>/dev/null)
  [ -n "$code" ] && [ "$code" != "000" ] || return 1
  return 0
}

notify_owner(){
  curl -s -m 6 -X POST "http://127.0.0.1:3000/send_private_msg" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":${OWNER_QQ:-0},\"message\":\"$1\"}" >/dev/null 2>&1
}

restart_host(){
  now=$(date +%s); last=$(cat "$LAST_HOST_BOOT" 2>/dev/null || echo 0)
  fails=$(cat "$HOST_FAILS" 2>/dev/null || echo 0)
  if [ "$fails" -ge 3 ]; then
    log "宿主连续 3 次没起来，停止自动重试，等人工"
    notify_owner "DSH 宿主连试三次都没起来，我不再自动重启了。需要你看一眼。"
    return
  fi
  echo "$now" > "$LAST_HOST_BOOT"
  log "拉起 DSH 宿主"
  ( cd /root && nohup node "$DSH_BIN" web --no-open >> "$DSH_HOST_LOG" 2>&1 & )
  sleep 10
  if host_ok; then
    echo 0 > "$HOST_FAILS"
    url=$(grep -o "http://127.0.0.1:3080/?token=[A-Za-z0-9_-]*" "$DSH_HOST_LOG" | tail -1)
    log "宿主已恢复 $url"
    notify_owner "刚才 DSH 掉了一下，我把它拉回来了。新地址：$url"
  else
    echo $((fails+1)) > "$HOST_FAILS"
    log "宿主没起来（第 $((fails+1)) 次）"
  fi
}

while true; do
  if ! host_ok; then
    log "DSH 宿主不在，尝试重启"
    restart_host
  fi

  if ! pgrep -f "[w]atchdog.sh" >/dev/null; then
    log "看门狗不见了，重新拉起"
    ( cd "$DIR" && nohup bash watchdog.sh >/dev/null 2>&1 & )
  fi
  sleep 20
done
