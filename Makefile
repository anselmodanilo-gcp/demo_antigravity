APP := apps/whatsapp-lead-assistant
PROJECT ?= advance-mantis-506814-g5
REGION ?= us-central1

.PHONY: help ci test lint bootstrap warm cold secrets logs dlq destroy

help: ## Lista os alvos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

ci: lint test ## Verificações locais (mesmas do GitHub Actions)

lint: ## ruff + mypy + bandit
	cd $(APP) && ruff format --check . && ruff check . && mypy && bandit -q -c pyproject.toml -r src

test: ## pytest com cobertura
	cd $(APP) && PYTHONPATH=src pytest --cov

bootstrap: ## Bootstrap do projeto (uma vez; requer Owner)
	./infra/bootstrap/bootstrap.sh

warm: ## Antes da apresentação: 1 instância quente por serviço (evita cold start)
	for s in wa-ingress lead-agent leads-dashboard; do \
	  gcloud run services update $$s --project $(PROJECT) --region $(REGION) --min-instances 1 --quiet; done

cold: ## Depois da apresentação: volta a escalar até zero (custo zero ocioso)
	for s in wa-ingress lead-agent leads-dashboard; do \
	  gcloud run services update $$s --project $(PROJECT) --region $(REGION) --min-instances 0 --quiet; done

secrets: ## Cadastra as credenciais da Meta no Secret Manager (entrada oculta, nada fica no histórico)
	./infra/bootstrap/set-meta-secrets.sh

logs: ## Últimos logs do lead-agent
	gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="lead-agent"' \
	  --project $(PROJECT) --limit 50 --format='value(jsonPayload.message)'

dlq: ## Inspeciona mensagens na DLQ
	gcloud pubsub subscriptions pull inbound-dlq-inspect --project $(PROJECT) --limit 5

destroy: ## Remove a infraestrutura da demo
	cd infra/terraform/envs/demo && terraform destroy
