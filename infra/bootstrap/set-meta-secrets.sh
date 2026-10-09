#!/usr/bin/env bash
# Cadastra as credenciais da Meta como nova versão dos segredos. Os valores são lidos com entrada
# oculta e enviados via stdin: não aparecem na tela, no histórico do shell nem em argumentos.
set -euo pipefail
PROJECT_ID="${PROJECT_ID:-advance-mantis-506814-g5}"

put() {
  local secret="$1" prompt="$2" value
  read -r -s -p "$prompt: " value; echo
  [ -n "$value" ] || { echo "valor vazio, ignorando $secret"; return; }
  printf '%s' "$value" | gcloud secrets versions add "$secret" --project "$PROJECT_ID" --data-file=- --quiet >/dev/null
  unset value
  echo "  ✓ $secret atualizado"
}

put wla-meta-app-secret      "App Secret (Meta → App settings → Basic)"
put wla-meta-access-token    "Token permanente do System User (WhatsApp)"
put wla-meta-phone-number-id "Phone Number ID (WhatsApp → API Setup)"

echo "Reiniciando serviços para carregar os novos valores..."
for s in wa-ingress lead-agent; do
  gcloud run services update "$s" --project "$PROJECT_ID" --region us-central1 \
    --update-labels "secrets-rotated=$(date +%s)" --quiet >/dev/null
done
echo "Pronto. Verify Token para cadastrar o webhook na Meta:"
echo "  gcloud secrets versions access latest --secret=wla-meta-verify-token --project $PROJECT_ID"
