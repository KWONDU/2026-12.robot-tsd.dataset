#!/usr/bin/env bash
# 대시보드를 계속 떠 있게 한다: scripts/keep_dashboard.sh
# 터미널과 분리된 감시 루프가 대시보드를 띄우고, 죽으면 RESTART_DELAY_S초 뒤 다시 띄운다.
# 감시 루프가 이미 돌고 있으면 아무것도 하지 않는다. 로그는 logs/dashboard.log, 재시작 기록은 logs/RUNS_STATUS.log.
# 끄기: kill "$(cat tmp/dashboard_keeper.pid)" 후 대시보드 프로세스도 종료 (pgrep -f '^.conda/bin/python -m tsdgen').
# 컨테이너가 재시작되면 이 루프도 사라진다. Claude Code 세션 시작 hook(.claude/settings.local.json)이 이 스크립트를 실행한다.
set -euo pipefail

cd "$(dirname "$0")/.."
pidfile=tmp/dashboard_keeper.pid
restart_delay_s=5
mkdir -p logs tmp

# 컨테이너 재시작 뒤에는 pidfile의 번호를 다른 프로세스가 쓰고 있을 수 있어 명령줄까지 확인한다.
if [[ -f "$pidfile" ]] && grep -qs "START dashboard" "/proc/$(cat "$pidfile")/cmdline"; then
  echo "dashboard keeper already running (pid $(cat "$pidfile"))"
  exit 0
fi

setsid nohup bash -c '
  export TMPDIR="$PWD/tmp"
  while true; do
    echo "$(date "+%F %T") START dashboard" >> logs/RUNS_STATUS.log
    TSDGEN_STAGE=dashboard .conda/bin/python -m tsdgen >> logs/dashboard.log 2>&1 < /dev/null || true
    echo "$(date "+%F %T") END dashboard (restart in $1s)" >> logs/RUNS_STATUS.log
    sleep "$1"
  done
' _ "$restart_delay_s" > /dev/null 2>&1 < /dev/null &
echo $! > "$pidfile"
echo "dashboard keeper started (pid $!), log: logs/dashboard.log"
