#!/usr/bin/env bash
# 장면 합성용 RoboCasa365 시뮬레이터 환경을 만든다: scripts/setup_robocasa_sim.sh
# 설치 정보(환경 위치, 저장소와 고정 커밋, 패키지)는 config/sources.yaml의 robocasa365.simulator에 있다.
# 이미 된 단계는 건너뛰므로 다시 돌려도 된다. 에셋(약 13GB)은 받는 데 20분쯤 걸리고 logs/robocasa_assets.log에 남는다.
set -euo pipefail

cd "$(dirname "$0")/.."
export TMPDIR="$PWD/tmp" PIP_CACHE_DIR="$PWD/tmp/pip_cache"
read_cfg() { .conda/bin/python -c "import yaml,sys; s=yaml.safe_load(open('config/sources.yaml'))['robocasa365']['simulator']; print(eval(sys.argv[1], {}, {'s': s}))" "$1"; }

venv=$(read_cfg "s['venv']")
root=$(read_cfg "s['root']")
mkdir -p "$root" logs tmp

for name in robosuite robocasa; do
  url=$(read_cfg "s['repos']['$name']['url']")
  commit=$(read_cfg "s['repos']['$name']['commit']")
  [ -d "$root/$name/.git" ] || git clone -q "$url" "$root/$name"
  git -C "$root/$name" fetch -q origin
  git -C "$root/$name" checkout -q "$commit"
done

[ -x "$venv/bin/python" ] || .conda/bin/python -m venv "$venv"
"$venv/bin/pip" install -q --upgrade pip
"$venv/bin/pip" install -q -e "$root/robosuite"
"$venv/bin/pip" install -q --no-deps -e "$root/robocasa"
read_cfg "' '.join(s['packages'])" | xargs "$venv/bin/pip" install -q

[ -f "$root/robosuite/robosuite/macros_private.py" ] || "$venv/bin/python" "$root/robosuite/robosuite/scripts/setup_macros.py"
[ -f "$root/robocasa/robocasa/macros_private.py" ] || "$venv/bin/python" -m robocasa.scripts.setup_macros
if [ ! -d "$root/robocasa/robocasa/models/assets/objects/lightwheel" ]; then
  yes y | "$venv/bin/python" -m robocasa.scripts.download_kitchen_assets --type all > logs/robocasa_assets.log 2>&1
fi
echo "ready: $venv ($(git -C "$root/robocasa" rev-parse --short HEAD) robocasa, $(git -C "$root/robosuite" rev-parse --short HEAD) robosuite)"
