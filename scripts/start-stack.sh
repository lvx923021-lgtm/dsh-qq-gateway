#!/usr/bin/env bash
# 按优先级起：互保层 → 看门狗 → 网关。只启动，不杀任何东西。
# 每一层都是"抢锁等 10 秒"：抢不到说明有健康的一份在跑，跳过；抢到说明前一份已死，接手。
set -u
D=/root/qq-gateway
cd "$D" || exit 1

echo "① 互保层"
if [ -f "$D/.supervise.pid" ] && kill -0 "$(cat "$D/.supervise.pid" 2>/dev/null)" 2>/dev/null; then
  echo "   已在（pid $(cat "$D/.supervise.pid")）"
else
  setsid nohup bash "$D/supervise.sh" >/dev/null 2>&1 &
  sleep 2
  echo "   启动 → pid $(cat "$D/.supervise.pid" 2>/dev/null || echo 未写)"
fi

echo "② 看门狗"
if [ -f "$D/.watchdog.pid" ] && kill -0 "$(cat "$D/.watchdog.pid" 2>/dev/null)" 2>/dev/null; then
  echo "   已在（pid $(cat "$D/.watchdog.pid")）"
else
  setsid nohup bash "$D/watchdog.sh" >/dev/null 2>&1 &
  sleep 2
  echo "   启动 → pid $(cat "$D/.watchdog.pid" 2>/dev/null || echo 未写)"
fi

echo "③ 网关"
if [ -f "$D/gateway.pid" ] && kill -0 "$(cat "$D/gateway.pid" 2>/dev/null)" 2>/dev/null; then
  echo "   已在（pid $(cat "$D/gateway.pid")）"
else
  set -a
  . "$D/.env"
  set +a
  setsid nohup python3 "$D/gateway.py" >> "$D/gateway.log" 2>&1 < /dev/null &
  sleep 6
  echo "   启动 → pid $(cat "$D/gateway.pid" 2>/dev/null || echo 未写)"
fi

echo "④ 状态"
for pair in "互保层:.supervise.pid" "看门狗:.watchdog.pid" "网关:gateway.pid"; do
  name=${pair%%:*}; f="$D/${pair##*:}"
  pid=$(cat "$f" 2>/dev/null || echo "")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "   $name 活着 pid=$pid"; else echo "   $name 没在跑"; fi
done
