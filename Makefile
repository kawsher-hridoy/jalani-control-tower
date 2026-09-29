.PHONY: up down ps logs test loadtest loadtest-decision benchmark benchmark-crisis clean

up:
	docker compose up -d --build

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=200

test:
	cd backend && uv run --with-requirements requirements.txt --with pytest pytest -q
	cd intel && uv run --with-requirements requirements.txt --with pytest pytest -q

# loadtest/results is bind-mounted into the k6 container, which writes the JSON
# summary as a non-root user; chmod it open first so a fresh clone can write to it.
loadtest:
	mkdir -p loadtest/results && chmod 777 loadtest/results
	docker run --rm --network host -v "$(CURDIR)/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/dashboard.js

loadtest-decision:
	mkdir -p loadtest/results && chmod 777 loadtest/results
	docker run --rm --network host -v "$(CURDIR)/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/decision.js

benchmark:
	docker compose exec backend python -m app.benchmark --sim http://sim-lab:8000 --ticks 288 --scenario baseline

benchmark-crisis:
	docker compose exec backend python -m app.benchmark --sim http://sim-lab:8000 --ticks 288 --scenario crisis

clean:
	docker compose down -v --remove-orphans
	rm -f loadtest/results/*.json
