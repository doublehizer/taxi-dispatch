# Система автоматической обработки и распределения заказов такси

Микросервисная система: принимает заказы пассажиров, рассчитывает стоимость,
подбирает ближайшего свободного водителя нужного класса и ведёт заказ
до завершения. Работает в Docker (локально) и в кластере Docker Swarm.

## Состав

| Сервис | Образ | Назначение |
|---|---|---|
| traefik | traefik:v3.1 | Точка входа, маршрутизация запросов, балансировка |
| web | собственный (nginx) | Панель диспетчера: вход, карта, заказы, управление водителями |
| auth-service | собственный (Python, FastAPI) | Учётные записи, вход по JWT, проверка прав для Traefik ForwardAuth |
| order-service | собственный (Python, FastAPI) | Приём заказов, расчёт цены, жизненный цикл заказа |
| driver-service | собственный (Python, FastAPI) | Водители, статусы, координаты, геоиндекс |
| dispatch-service | собственный (Python) | Воркер распределения заказов (несколько реплик) |
| simulator | собственный (Python) | Генератор нагрузки: водители и пассажиры |
| postgres | postgres:16-alpine | Заказы, водители, журнал статусов |
| redis | redis:7-alpine | Геоиндекс свободных водителей (GEOSEARCH) |
| rabbitmq | rabbitmq:3.13-management | Очереди orders.new и orders.retry |
| prometheus | prom/prometheus | Сбор метрик |
| grafana | grafana/grafana | Дашборд мониторинга |

Доступ к API проверяется на шлюзе: Traefik перед передачей запроса в
order-service или driver-service вызывает `auth-service /auth/verify`
(middleware ForwardAuth). Роли:

| Роль | Права |
|---|---|
| dispatcher | просмотр заказов и водителей, создание и отмена заказов |
| admin | всё то же + добавление, изменение и удаление водителей, управление пользователями |

Начальные учётные записи при локальном запуске: `admin / admin12345`,
`dispatcher / dispatcher123`. В Swarm пароли генерируются скриптом
deploy.sh и хранятся в Docker secrets.

Путь заказа:

```
Пассажир -> traefik -> order-service -> PostgreSQL
                             |
                             v
                     RabbitMQ: orders.new -> dispatch-service (xN) -> Redis GEOSEARCH
                             ^                       |
                             |                       v
                 orders.retry (TTL 5 с)      PostgreSQL: заказ назначен
```

Сети:

- `taxi_edge` - точка входа и веб-интерфейсы;
- `taxi_backend` - API, брокер, мониторинг;
- `taxi_data` - БД и Redis, `internal: true`, извне недоступна.

## Локальный запуск (macOS / Windows, Docker Desktop)

```bash
docker compose up -d --build
```

| Адрес | Что открывается |
|---|---|
| http://localhost | Панель диспетчера (клик по карте - новый заказ) |
| http://localhost/api/auth/login | Вход (POST, JSON: username, password) |
| http://localhost/grafana | Grafana, admin / admin |
| http://localhost:8080 | Панель Traefik |
| http://localhost:15672 | RabbitMQ, taxi / taxi |
| http://localhost:9090 | Prometheus |
| http://localhost/api/orders | REST API заказов |
| http://localhost/api/drivers | REST API водителей |

Полезные команды:

```bash
docker compose ps                                        # состояние контейнеров
docker compose logs -f dispatch-service                  # как распределяются заказы
docker compose up -d --scale dispatch-service=4          # масштабирование воркера
docker compose exec postgres psql -U taxi -d taxi        # консоль БД
docker compose exec redis redis-cli zcard drivers:free:economy
docker compose down -v                                   # остановить и удалить данные
```

## Кластер Docker Swarm

Кластер состоит из трёх узлов: `manager`, `worker1` (метка `tier=data`,
хранилища) и `worker2`. Сценарий `infra/swarm/create-cluster.sh` создаёт
узлы как отдельные экземпляры Docker Engine (Docker-in-Docker) и работает
одинаково в Docker Desktop на macOS и Windows, в Linux и в GitHub Actions.

```bash
infra/swarm/create-cluster.sh            # три узла, swarm init и join, метка узла
docker exec -it manager bash             # консоль управляющего узла
cd /taxi-dispatch/infra/swarm
LOCAL_BUILD=1 ./deploy.sh                # сборка образов на узле и развёртывание
# или REGISTRY=ghcr.io/<логин> TAG=latest ./deploy.sh   - образы из CI
```

Проверка на управляющем узле:

```bash
docker node ls
docker stack services taxi
docker service scale taxi_dispatch-service=5
docker service ps taxi_dispatch-service
```

Проверка через шлюз с хост-машины:

```bash
DISPATCHER_PASSWORD=<пароль> infra/swarm/smoke-test.sh
```

Отказ узла имитируется командой `docker stop worker2`: задачи остановленного
узла перезапускаются на оставшихся. Кластер удаляется сценарием
`infra/swarm/destroy-cluster.sh`.

Если шлюз нужен на другом порту: `HTTP_PORT=8000 infra/swarm/create-cluster.sh`.

Для стенда на отдельных виртуальных машинах в Windows предусмотрен сценарий
`infra/swarm/create-cluster.ps1` (Multipass, три ВМ Ubuntu Server). После него
проект копируется на manager, и `deploy.sh` запускается там же.

Configs в Swarm неизменяемы: после правки prometheus.swarm.yml или дашборда
выполните `docker stack rm taxi` и разверните стек заново.

## CI/CD (GitHub Actions)

Файл `.github/workflows/ci-cd.yml`:

1. **test** - ruff, pytest, проверка docker-compose.yml (на каждый push и pull request);
2. **build** - сборка шести образов и публикация в GitHub Container Registry
   с тегами `latest` и SHA коммита;
3. **deploy** - в облачной среде выполнения создаётся кластер из трёх узлов,
   в него разворачивается стек из опубликованных образов, затем
   `smoke-test.sh` проверяет шлюз, права доступа и распределение заказа.

Чтобы то же задание обновляло постоянный кластер, на его управляющий узел
ставится self-hosted runner (Settings → Actions → Runners), в задании deploy
указывается `runs-on: [self-hosted, swarm-manager]`, а шаг создания кластера
убирается. Runner сам подключается к GitHub, входящий доступ к узлу не нужен.

Файл `.github/workflows/demo.yml` запускается вручную и выполняет
демонстрационный прогон (`demo/run-demo.sh`): Docker Compose, кластер,
изоляция сети данных, масштабирование, отказ узла. Снимки окон терминала
и веб-интерфейсов сохраняются в артефакт `demo-screens`.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check .
```
