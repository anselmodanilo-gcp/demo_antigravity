# Política de segurança

Este repositório é uma **demonstração** e usa apenas dados fictícios.

## Como reportar uma vulnerabilidade

Não abra issue pública. Use **Security → Report a vulnerability** (GitHub Private Vulnerability
Reporting) neste repositório. Respondemos em até 5 dias úteis.

## Práticas adotadas

- Nenhuma chave de service account: o deploy usa Workload Identity Federation (OIDC do GitHub),
  restrito ao branch `main` deste repositório e com aprovação manual no ambiente `demo`.
- Segredos somente no Secret Manager; o app falha fechado enquanto houver placeholders.
- Webhook autenticado por HMAC-SHA256 (App Secret da Meta) com comparação em tempo constante.
- Painel protegido pelo IAP + validação do JWT no app (defesa em profundidade).
- Menor privilégio: uma service account por serviço, papéis no nível do recurso.
- Cadeia de suprimentos: dependências travadas com hash, actions fixadas por SHA, imagem base
  por digest, Dependabot, CodeQL, gitleaks, Trivy, pip-audit, bandit e OpenSSF Scorecard.
- LGPD: telefone pseudonimizado (HMAC com sal secreto), mascarado no painel, sem PII nos logs e
  no BigQuery, retenção com TTL e comando "apagar meus dados".
