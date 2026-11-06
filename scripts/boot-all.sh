#!/usr/bin/env bash
# 一条启动链：QQ 在线 → 网关 → 看门狗。幂等，重复跑没副作用。
set -u
D=/root/qq-gateway
LOG=$D/boot-all.log

log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# QQ 的输出会无限长：超过 20MB 就只留最后 2MB
log_trim(){
  local f=/root/napcat/qq-up.log
  [ -f "$f" ] || return 0
  local sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt 20971520 ]; then
    tail -c 2000000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    log "qq-up.log 从 $((sz/1048576))MB 截到 2MB"
  fi
}
log_trim

onebot_ok(){
  python3 - <<'PY'
import json, sys, urllib.request
try:
    req = urllib.request.Request("http://127.0.0.1:3000/get_login_info",
        data=b"{}", headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=5))
    sys.exit(0 if d.get("retcode") == 0 else 1)
except Exception:
    sys.exit(1)
PY
}

gw_alive(){ ps -eo args | grep -q "[q]q-gateway/gateway\.py"; }
wd_alive(){ ps -eo args | awk '$1=="bash"' | grep -q "[q]q-gateway/watchdog\.sh"; }

# 按优先级串行：先把看护的立起来，再拉被看护的。每一步等它就绪才走下一步。
sp_alive(){ ps -eo args | awk '$1=="bash" && $3 ~ /supervise\.sh$/' | grep -q .; }
wait_for(){  # wait_for <描述> <秒数> <命令...>
  local what=$1 secs=$2; shift 2
  local i=0
  while [ $i -lt $secs ]; do
    "$@" && return 0
    sleep 1; i=$((i+1))
  done
  log "等 $what 超时（${secs}s）"; return 1
}

# ① 互保层（最高优先级：它负责让别人一直活着）
if sp_alive; then
  log "① 互保层已在"
else
  log "① 拉起互保层"
  ( cd "$D" && setsid nohup bash supervise.sh >/dev/null 2>&1 & )
  sleep 1
fi

# ② 看门狗
if wd_alive; then
  log "② 看门狗已在"
else
  log "② 拉起看门狗"
  ( cd "$D" && setsid nohup bash watchdog.sh >/dev/null 2>&1 & )
  sleep 1
fi

# ③ QQ / OneBot
if onebot_ok; then
  log "③ OneBot 已在线"
else
  log "③ 拉起 QQ"
  bash "$D/qq-up.sh" >> "$LOG" 2>&1
  wait_for OneBot 90 onebot_ok || true
fi

# ④ 网关
if gw_alive; then
  log "④ 网关已在"
else
  log "④ 拉起网关"
  ( cd "$D" && set -a && . ./.env && set +a \
    && setsid nohup python3 "$D/gateway.py" >> "$D/gateway.log" 2>&1 & )
  wait_for 网关 20 gw_alive || true
fi

log "完成：$(onebot_ok && echo OneBot在线 || echo OneBot离线)，网关 $(gw_alive && echo 在 || echo 不在)"
