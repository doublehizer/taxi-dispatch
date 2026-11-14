#!/usr/bin/env bash
# Развёртывание стека на управляющем узле кластера.
#
#   REGISTRY=ghcr.io/<логин> TAG=latest ./deploy.sh   — образы из GitHub Container Registry
#   LOCAL_BUILD=1 ./deploy.sh                          — сборка на узле и локальный реестр кластера
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
STACK="${STACK:-taxi}"
TAG="${TAG:-latest}"
SERVICES=(auth-service order-service driver-service dispatch-service simulator web)

if [[ "${LOCAL_BUILD:-0}" == "1" ]]; then
    REGISTRY="127.0.0.1:5000"
    if ! docker service inspect registry >/dev/null 2>&1; then
        echo ">> Запуск локального реестра образов"
        docker service create --name registry --publish 5000:5000 \
            --constraint node.role==manager registry:2
        sleep 5
    fi
    for svc in "${SERVICES[@]}"; do
        echo ">> Сборка $svc"
        docker build -t "$REGISTRY/taxi-$svc:$TAG" -f "$ROOT/services/$svc/Dockerfile" "$ROOT"
        docker push "$REGISTRY/taxi-$svc:$TAG"
    done
fi

: "${REGISTRY:?Задайте REGISTRY, например ghcr.io/login, или LOCAL_BUILD=1}"
export REGISTRY TAG

# Секреты создаются один раз
create_secret() {
    local name="$1" value="$2" show="${3:-0}"
    if ! docker secret inspect "$name" >/dev/null 2>&1; then
        printf '%s' "$value" | docker secret create "$name" - >/dev/null
        echo ">> Создан секрет $name"
        if [[ "$show" == "1" ]]; then
            echo "   значение: $value"
        fi
    fi
}
create_secret pg_password "$(openssl rand -hex 16)"
create_secret jwt_secret "$(openssl rand -hex 32)"
# Если пароль не задан переменной окружения, он генерируется и выводится один раз
create_secret admin_password "${ADMIN_PASSWORD:-$(openssl rand -hex 6)}" "$([[ -z "${ADMIN_PASSWORD:-}" ]] && echo 1 || echo 0)"
create_secret dispatcher_password "${DISPATCHER_PASSWORD:-$(openssl rand -hex 6)}" "$([[ -z "${DISPATCHER_PASSWORD:-}" ]] && echo 1 || echo 0)"

echo ">> Развёртывание стека $STACK (образы $REGISTRY, тег $TAG)"
docker stack deploy --with-registry-auth --prune --detach=false -c docker-stack.yml "$STACK"

docker stack services "$STACK"
