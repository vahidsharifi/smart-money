.PHONY: up down logs rebuild migrate reset_db api-shell smoke smoke_db smoke_redis smoke_listener smoke_decoder smoke_risk smoke_profiler smoke_alerts smoke_narrator smoke_api

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

rebuild:
	docker compose up --build --force-recreate

migrate:
	docker compose exec api alembic upgrade head

reset_db:
	docker compose down -v
	docker compose up -d db

api-shell:
	docker compose exec api bash

smoke_db:
	docker compose exec -T api python -m app.scripts.smoke_db

smoke_redis:
	docker compose exec -T api python -m app.scripts.smoke_redis

smoke_listener:
	docker compose exec -T api python -m app.scripts.smoke_listener

smoke_decoder:
	docker compose exec -T api python -m app.scripts.smoke_decoder

smoke_risk:
	docker compose exec -T api python -m app.scripts.smoke_risk

smoke_profiler:
	docker compose exec -T api python -m app.scripts.smoke_profiler

smoke_alerts:
	docker compose exec -T api python -m app.scripts.smoke_alerts

smoke_narrator:
	docker compose exec -T api python -m app.scripts.smoke_narrator

smoke_api:
	docker compose exec -T api python -m app.scripts.smoke_api

smoke:
	$(MAKE) smoke_db
	$(MAKE) smoke_redis
	$(MAKE) smoke_listener
	$(MAKE) smoke_decoder
	$(MAKE) smoke_risk
	$(MAKE) smoke_profiler
	$(MAKE) smoke_alerts
	$(MAKE) smoke_narrator
	$(MAKE) smoke_api
