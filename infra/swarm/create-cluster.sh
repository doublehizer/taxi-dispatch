#!/usr/bin/env bash
# Создание кластера Docker Swarm из трёх узлов: manager, worker1, worker2.
#
# Каждый узел - отдельный экземпляр Docker Engine в привилегированном
# контейнере (Docker-in-Docker) со своим сетевым стеком и хранилищем.
# Способ одинаково работает в Docker Desktop (macOS, Windows), в Linux
# и на облачных исполнителях GitHub Actions.
#
#   ./create-cluster.sh               создать кластер
#   HTTP_PORT=8000 ./create-cluster.sh   опубликовать шлюз на другом порту
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
NETWORK="${CLUSTER_NETWORK:-taxi-cluster}"
NODE_IMAGE="${NODE_IMAGE:-docker:27-dind}"
HTTP_PORT="${HTTP_PORT:-80}"
MANAGER=manager
WORKERS=(worker1 worker2)

docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null

start_node() {
    local name="$1"; shift
    echo ">> Запуск узла $name"
    docker run -d --privileged --name "$name" --hostname "$name" \
        --network "$NETWORK" --restart unless-stopped \
        -v "taxi-node-$name:/var/lib/docker" "$@" "$NODE_IMAGE" >/dev/null
}

# Порты шлюза и служебных интерфейсов публикуются через управляющий узел
start_node "$MANAGER" \
    -p "$HTTP_PORT:80" -p 8080:8080 -p 9090:9090 -p 15672:15672 \
    -v "$ROOT:/taxi-dispatch:ro"
for node in "${WORKERS[@]}"; do
    start_node "$node"
done

for node in "$MANAGER" "${WORKERS[@]}"; do
    for _ in $(seq 1 60); do
        docker exec "$node" docker info >/dev/null 2>&1 && break
        sleep 1
    done
    docker exec "$node" docker info >/dev/null
done

# Инструменты, которые нужны сценарию deploy.sh на управляющем узле
docker exec "$MANAGER" apk add --no-cache --quiet bash curl

MANAGER_IP="$(docker exec "$MANAGER" hostname -i | awk '{print $1}')"
docker exec "$MANAGER" docker swarm init --advertise-addr "$MANAGER_IP" >/dev/null
TOKEN="$(docker exec "$MANAGER" docker swarm join-token -q worker)"
for node in "${WORKERS[@]}"; do
    docker exec "$node" docker swarm join --token "$TOKEN" "$MANAGER_IP:2377"
done

# Узел worker1 выделяется под хранилища данных
docker exec "$MANAGER" docker node update --label-add tier=data worker1 >/dev/null

docker exec "$MANAGER" docker node ls
echo ">> Кластер готов. Развёртывание: docker exec -it manager bash, затем"
echo "   cd /taxi-dispatch/infra/swarm && LOCAL_BUILD=1 ./deploy.sh"
