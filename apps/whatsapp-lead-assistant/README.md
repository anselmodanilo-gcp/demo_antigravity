# WhatsApp Lead Assistant (caso 4)

Assistente de atendimento no WhatsApp com Gemini que qualifica leads do lançamento fictício
**Residencial Jardins** (Incorporadora Horizonte), avisa o corretor quando o lead fica quente e
alimenta um painel interno. Todos os dados são fictícios.

| Serviço | Módulo | Exposição |
| --- | --- | --- |
| `wa-ingress` | `wla.ingress.app` | Público (assinatura HMAC da Meta) |
| `lead-agent` | `wla.agent.app` | Interno (push do Pub/Sub com OIDC) |
| `leads-dashboard` | `wla.dashboard.app` | IAP (somente o administrador) |

Uma única imagem atende os três serviços (`WLA_APP=ingress|agent|dashboard`).

## Desenvolvimento

```bash
pip install -r requirements-dev.txt
ruff format --check . && ruff check . && mypy && pytest --cov
```
