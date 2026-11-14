.PHONY: up down logs ps scale test lint reset

up:            ## Собрать образы и запустить систему
	docker compose up -d --build

down:          ## Остановить систему
	docker compose down

reset:         ## Остановить и удалить все данные (тома)
	docker compose down -v

logs:          ## Логи распределения заказов
	docker compose logs -f dispatch-service

ps:            ## Состояние контейнеров
	docker compose ps

scale:         ## Масштабировать воркер распределения: make scale N=4
	docker compose up -d --no-deps --scale dispatch-service=$(N) dispatch-service

test:          ## Модульные тесты
	pytest -v

lint:          ## Статический анализ
	ruff check .
