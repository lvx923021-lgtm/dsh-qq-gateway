#!/usr/bin/env bash
# QQ 机器人看门狗 v2
# 原则：宁可停着等人工，也不为了"看起来在跑"去反复登录——反复登录是封号直通车。
set -u

UIN=${BOT_UIN:-}
NAP_HOME=/opt/QQ
GW=/root/qq-gateway/gateway.py
LOGDIR=/root/qq-gateway
LOG=$LOGDIR/watchdog.log
LOCK=$LOGDIR/.watchdog.pid
STATE=$LOGDIR/.state            # normal | wait_human
LASTBOOT=$LOGDIR/.last_boot      # 上次拉起 QQ 的时间戳
BOOT_COOLDOWN=900                # 两次自动拉起至少间隔 15 分钟

mkdir -p "$LOGDIR"
# 抢锁：同一时刻只允许一份；抢不到直接退，不再"先查再写"
LOCKFILE="$LOGDIR/.watchdog.lock"
exec 9>"$LOCKFILE"
flock -w 10 9 || { echo "等了 10 秒仍有一份健康的在跑，退出"; exit 0; }
echo $$ > "$LOCK"
notify(){   # 出事时给他发一条 QQ
  curl -s -m 6 -X POST "http://127.0.0.1:3000/send_private_msg" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":${OWNER_QQ:-0},\"message\":\"$1\"}" >/dev/null 2>&1
}
log(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }
state(){ cat "$STATE" 2>/dev/null || echo normal; }

gw_alive(){
  pgrep -f "[g]ateway.py" >/dev/null || return 1
  local hb=$LOGDIR/.gw-heartbeat
  [ -f "$hb" ] || return 1
  local age=$(( $(date +%s) - $(stat -c %Y "$hb" 2>/dev/null || echo 0) ))
  [ "$age" -lt 120 ] || return 1
  return 0
}

qq_alive(){
  pgrep -x qq >/dev/null && return 0     # 进程在，直接算活
  onebot_ok && return 0                  # 进程名查不到，但接口通——也活着
  return 1
}

onebot_ok(){ curl -s -m 5 -X POST "http://127.0.0.1:3000/get_login_info" -d '{}' 2>/dev/null | grep -q '"retcode":0'; }

# QQ 是不是停在"等你扫码"上
waiting_scan(){
  local f=$1
  [ -f "$f" ] || return 1
  tail -c 4000 "$f" 2>/dev/null | grep -q "请扫描下面的二维码"
}

boot_qq(){
  local now last
  now=$(date +%s); last=$(cat "$LASTBOOT" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt $BOOT_COOLDOWN ]; then
    log "冷却中（$(( (BOOT_COOLDOWN-(now-last))/60 )) 分钟），不重启"
    return 1
  fi
  echo "$now" > "$LASTBOOT"
  log "拉起 QQ"
  ( cd "$NAP_HOME" && nohup xvfb-run -a ./qq --no-sandbox --disable-dev-shm-usage -q "$UIN" \
      > /root/napcat/qq-daemon.log 2>&1 & )
  sleep 45
}

start_gw(){
  [ -f "$LOGDIR/.env" ] || { log "缺 .env"; return; }
  log "拉起网关"
  ( cd "$LOGDIR" && set -a && . ./.env && set +a && nohup python3 "$GW" >> "$LOGDIR/gateway.log" 2>&1 & )
}


# ---- DSHA App 掉线监护 ----
DAPP=dsh.android.client
DACT=com.deepseekharness.app.ui.MainActivity
DLAST=$LOGDIR/.last_dsha_boot
DTICK=0

dsha_ok(){
  local c
  c=$(curl -s -m 3 -o /dev/null -w "%{http_code}" -H @/root/.dsh/.bridge_headers http://127.0.0.1:3090/app/device 2>/dev/null)
  [ -n "$c" ] && [ "$c" != "000" ]
}

dsha_revive(){
  local now last
  now=$(date +%s); last=$(cat "$DLAST" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt 300 ]; then
    return 1                    # 五分钟冷却，别反复折腾
  fi
  echo "$now" > "$DLAST"
  log "DSHA 无响应，尝试拉起 App"
  /root/dsh-bin/adb-shell "am start -n $DAPP/$DACT" >> "$LOG" 2>&1
  sleep 8
  if dsha_ok; then
    log "DSHA 已恢复"
    notify "刚才 DSHA 掉线了，我把它拉起来了。"
    return 0
  fi
  log "DSHA 仍无响应"
  notify "DSHA 掉线了，我拉了一次没起来，你可能得手动打开一下 App。"
  return 1
}

log "看门狗 v3 启动 pid=$$ 状态=$(state)"
fail=0

while true; do
  if ! qq_alive; then
    if [ "$(state)" = wait_human ]; then
      log "QQ 未运行，但处于待扫码状态，不自动重启"
    else
      boot_qq
      if waiting_scan /root/napcat/qq-daemon.log; then
        echo wait_human > "$STATE"
        log "！！需要人工扫码，已进入待命状态，不再自动重启"
      fi
    fi
    sleep 10
    continue
  fi

  if onebot_ok; then
    fail=0
    if [ "$(state)" = wait_human ]; then
      echo normal > "$STATE"; log "已登录，状态恢复正常"
    fi
    if ! gw_alive; then
      if pgrep -f "[g]ateway.py" >/dev/null; then
        log "网关进程在但没心跳，判死重启"
        pkill -f "[g]ateway.py" 2>/dev/null; sleep 2
      fi
      start_gw
    fi
  else
    fail=$((fail+1))
    if waiting_scan /root/napcat/qq-daemon.log; then
      echo wait_human > "$STATE"
      log "检测到等待扫码，进入待命，不再重启"
    elif [ "$fail" -ge 6 ]; then
      log "OneBot 连续 $fail 次无响应，重启 QQ（受冷却限制）"
      pkill -x qq 2>/dev/null; pkill -x Xvfb 2>/dev/null
      sleep 3
      boot_qq
      fail=0
      if waiting_scan /root/napcat/qq-daemon.log; then
        echo wait_human > "$STATE"; log "拉起后仍需扫码，进入待命"
      fi
    fi
  fi
  DTICK=$((DTICK+1))
  if [ $((DTICK % 3)) -eq 0 ] && ! dsha_ok; then
    dsha_revive
  fi
  sleep 10
done
