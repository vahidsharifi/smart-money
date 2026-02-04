.PHONY: up down logs rebuild migrate reset_db api-shell

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
