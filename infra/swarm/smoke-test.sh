#!/usr/bin/env bash
# Проверка развёрнутой системы через шлюз: доступность, разграничение
# прав и прохождение заказа до назначения водителя.
#
#   BASE_URL=http://127.0.0.1 DISPATCHER_PASSWORD=... ./smoke-test.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1}"
API="$BASE_URL/api"
PASSWORD="${DISPATCHER_PASSWORD:?Задайте DISPATCHER_PASSWORD}"

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
expect() {
    local title="$1" want="$2" got="$3"
    if [[ "$want" == "$got" ]]; then
        echo "OK    $title: $got"
    else
        echo "FAIL  $title: ожидалось $want, получено $got"; exit 1
    fi
}

echo ">> Ожидание готовности шлюза и сервиса аутентификации"
for _ in $(seq 1 60); do
    [[ "$(code "$API/orders")" == "401" ]] && break
    sleep 5
done

expect "запрос без токена" 401 "$(code "$API/orders")"
expect "неверный пароль" 401 "$(code -X POST "$API/auth/login" \
    -H 'Content-Type: application/json' -d '{"username":"dispatcher","password":"wrong"}')"

TOKEN="$(curl -fsS -X POST "$API/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"dispatcher\",\"password\":\"$PASSWORD\"}" | field access_token)"
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

expect "список заказов с токеном" 200 "$(code "${AUTH[@]}" "$API/orders")"
expect "добавление водителя диспетчером" 403 "$(code -X POST "${AUTH[@]}" "$API/drivers" \
    -d '{"name":"Test","car":"Kia Rio","plate":"T000TT00","lat":55.75,"lon":37.61}')"

ORDER_ID="$(curl -fsS -X POST "${AUTH[@]}" "$API/orders" \
    -d '{"passenger_name":"Smoke test","tariff":"economy","pickup":{"lat":55.7558,"lon":37.6173},"dropoff":{"lat":55.79,"lon":37.56}}' \
    | field id)"
echo "OK    создан заказ $ORDER_ID"

STATUS=new
for _ in $(seq 1 30); do
    STATUS="$(curl -fsS "${AUTH[@]}" "$API/orders/$ORDER_ID" | field status)"
    case "$STATUS" in assigned|in_progress|completed) break ;; esac
    sleep 2
done
case "$STATUS" in
    assigned|in_progress|completed) echo "OK    заказ $ORDER_ID распределён, статус $STATUS" ;;
    *) echo "FAIL  заказ $ORDER_ID не распределён, статус $STATUS"; exit 1 ;;
esac
echo ">> Все проверки пройдены"
