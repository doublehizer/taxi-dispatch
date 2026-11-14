#!/usr/bin/env bash
# Демонстрационный прогон: локальный запуск через Docker Compose, затем
# кластер Docker Swarm из трёх узлов. Каждый шаг фиксируется снимком
# окна терминала или браузера в каталоге demo/out.
#
# Требуются: docker, Xvfb, xterm, xdotool, ImageMagick, Playwright (Chromium).
# Запуск: xvfb-run -s '-screen 0 2400x1700x24' demo/run-demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."
source demo/term.sh
OUT="$PWD/demo/out"
mkdir -p "$OUT"

ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin12345}"
DISPATCHER_PASSWORD="${DISPATCHER_PASSWORD:-dispatcher123}"
export ADMIN_PASSWORD DISPATCHER_PASSWORD
NODE_SHELL=(docker exec -it -e "PS1=root@manager:\\w# " manager bash --norc)
HOST_SHELL=(env "PS1=\\u@\\h:\\W\\$ " bash --norc)

step() { echo; echo "=================== $* ==================="; }

wait_stack() {
    for _ in $(seq 1 90); do
        if ! docker exec manager docker stack services taxi --format '{{.Replicas}}' \
            | awk -F/ '$1 != $2 { bad = 1 } END { exit bad }'; then
            sleep 5
        else
            return 0
        fi
    done
    docker exec manager docker stack services taxi
    return 1
}

# ---------- 1. Docker Compose ----------
step "Docker Compose"
docker compose up -d --build --wait
sleep 45   # симулятор успевает создать водителей и первые заказы

term_open 112 22 "${HOST_SHELL[@]}"
term_run 4 "docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'"
term_shot "$OUT/compose-ps.png"
term_close

python demo/web_shots.py panel http://127.0.0.1 "$OUT" "$ADMIN_PASSWORD"
docker compose down -v

# ---------- 2. Кластер Docker Swarm ----------
step "Создание кластера"
infra/swarm/create-cluster.sh
docker exec -e LOCAL_BUILD=1 -e ADMIN_PASSWORD -e DISPATCHER_PASSWORD \
    -w /taxi-dispatch/infra/swarm manager ./deploy.sh
wait_stack
STARTED=$(date +%s)
BASE_URL=http://127.0.0.1 infra/swarm/smoke-test.sh

step "Узлы и сервисы"
term_open 118 30 "${NODE_SHELL[@]}"
term_run 3 "docker node ls"
term_run 4 "docker stack services taxi --format 'table {{.Name}}\t{{.Mode}}\t{{.Replicas}}\t{{.Image}}'"
term_shot "$OUT/cluster-services.png"
term_close

step "Изоляция сети данных"
term_open 118 22 "${NODE_SHELL[@]}"
term_run 3 "docker network ls --filter driver=overlay"
term_run 3 'T=$(docker ps -qf name=taxi_traefik | head -1); D=$(docker ps -qf name=taxi_driver | head -1)'
term_run 6 'docker exec $T nc -zv -w 3 redis 6379'
term_run 6 'docker exec $T nc -zv -w 3 order-service 8000'
term_run 6 'docker exec $D bash -c "echo > /dev/tcp/redis/6379 && echo redis:6379 open"'
term_shot "$OUT/network-isolation.png"
term_close

step "Масштабирование"
term_open 118 24 "${NODE_SHELL[@]}"
term_run 50 "docker service scale taxi_dispatch-service=5"
term_run 4 "docker service ps taxi_dispatch-service -f desired-state=running --format 'table {{.Name}}\t{{.Node}}\t{{.CurrentState}}'"
term_shot "$OUT/scale.png"
term_close

# Дашборду нужно несколько минут данных
ELAPSED=$(( $(date +%s) - STARTED ))
if (( ELAPSED < 420 )); then sleep $(( 420 - ELAPSED )); fi
step "Grafana"
python demo/web_shots.py grafana http://127.0.0.1 "$OUT"
mkdir -p "$OUT/cluster"
python demo/web_shots.py panel http://127.0.0.1 "$OUT/cluster" "$ADMIN_PASSWORD" || true

step "Отказ узла worker2"
docker stop worker2
sleep 75
wait_stack || true
term_open 118 24 "${NODE_SHELL[@]}"
term_run 3 "docker node ls"
term_run 4 "docker service ps taxi_order-service --format 'table {{.Name}}\t{{.Node}}\t{{.DesiredState}}\t{{.CurrentState}}'"
term_shot "$OUT/node-failure.png"
term_close
BASE_URL=http://127.0.0.1 infra/swarm/smoke-test.sh

step "Готово"
ls -la "$OUT"
