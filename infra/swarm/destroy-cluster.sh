#!/usr/bin/env bash
# Удаление кластера, созданного сценарием create-cluster.sh, вместе с данными узлов.
set -euo pipefail

for node in manager worker1 worker2; do
    docker rm -f "$node" >/dev/null 2>&1 || true
    docker volume rm "taxi-node-$node" >/dev/null 2>&1 || true
done
docker network rm "${CLUSTER_NETWORK:-taxi-cluster}" >/dev/null 2>&1 || true
echo ">> Кластер удалён"
