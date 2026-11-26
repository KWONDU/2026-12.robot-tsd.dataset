#!/usr/bin/env bash
# 스테이지를 터미널과 분리해서 끝까지 돌린다: scripts/run_detached.sh <스테이지> [로그 이름]
# 로그는 logs/<이름>.log (tail -f 로 확인), 시작·종료 기록은 logs/RUNS_STATUS.log 에 한 줄씩 남는다.
set -euo pipefail

cd "$(dirname "$0")/.."
stage="${1:?사용법: scripts/run_detached.sh <스테이지> [로그 이름]}"
name="${2:-${stage}_$(date '+%Y%m%d_%H%M%S')}"
python=.conda/bin/python

mkdir -p logs tmp
export TMPDIR="$PWD/tmp"
echo "$(date '+%F %T') START ${name} stage=${stage}" >> logs/RUNS_STATUS.log

setsid nohup bash -c '
  TSDGEN_STAGE="$1" "$2" -m tsdgen
  code=$?
  echo "$(date "+%F %T") END $3 exit=${code}" >> logs/RUNS_STATUS.log
' _ "$stage" "$python" "$name" > "logs/${name}.log" 2>&1 < /dev/null &

echo "started ${name} (pid $!): tail -f logs/${name}.log"
