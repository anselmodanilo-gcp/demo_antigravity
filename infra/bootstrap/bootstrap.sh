#!/usr/bin/env bash
# Bootstrap único e idempotente do projeto (executado por um humano com papel de Owner).
#
# Cria apenas o que o pipeline precisa para operar sem chaves:
#   - APIs base, bucket do estado do Terraform e repositório do Artifact Registry;
#   - service account do deploy + Workload Identity Federation restrito ao main deste repo;
#   - alerta de orçamento.
# Tudo o mais é criado pelo Terraform (infra/terraform/envs/demo) via GitHub Actions.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-advance-mantis-506814-g5}"
REGION="${REGION:-us-central1}"
GITHUB_REPO="${GITHUB_REPO:-anselmodanilo-gcp/demo_antigravity}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@anselmodanilo.altostrat.com}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-010DB4-361CFB-EAA35B}"
BUDGET_USD="${BUDGET_USD:-10}"

STATE_BUCKET="${PROJECT_ID}-tfstate"
DEPLOYER="github-deployer"
DEPLOYER_EMAIL="${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL="github"
PROVIDER="demo-antigravity"
AR_REPO="demo-antigravity"

gc() { gcloud --project "$PROJECT_ID" --quiet "$@" </dev/null; }
log() { printf '\n==> %s\n' "$*"; }

PROJECT_NUMBER="$(gc projects describe "$PROJECT_ID" --format='value(projectNumber)')"

log "Habilitando APIs"
gc services enable \
  iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com \
  serviceusage.googleapis.com artifactregistry.googleapis.com run.googleapis.com firestore.googleapis.com \
  pubsub.googleapis.com secretmanager.googleapis.com bigquery.googleapis.com aiplatform.googleapis.com \
  iap.googleapis.com monitoring.googleapis.com billingbudgets.googleapis.com cloudbilling.googleapis.com

log "Bucket do estado do Terraform: gs://${STATE_BUCKET}"
if ! gc storage buckets describe "gs://${STATE_BUCKET}" >/dev/null 2>&1; then
  gc storage buckets create "gs://${STATE_BUCKET}" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
gc storage buckets update "gs://${STATE_BUCKET}" --versioning >/dev/null

log "Artifact Registry: ${AR_REPO}"
if ! gc artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1; then
  gc artifacts repositories create "$AR_REPO" --location="$REGION" --repository-format=docker \
    --immutable-tags --description="Imagens da demo Antigravity para Business"
fi
POLICY_FILE="$(mktemp)"
cat >"$POLICY_FILE" <<'JSON'
[
  {"name": "manter-5-mais-recentes", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "apagar-antigas", "action": {"type": "Delete"}, "condition": {"olderThan": "7d"}}
]
JSON
gc artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" \
  --policy="$POLICY_FILE" --no-dry-run >/dev/null
rm -f "$POLICY_FILE"

log "Service account do deploy: ${DEPLOYER_EMAIL}"
if ! gc iam service-accounts describe "$DEPLOYER_EMAIL" >/dev/null 2>&1; then
  gc iam service-accounts create "$DEPLOYER" --display-name="GitHub Actions deploy (WIF, sem chave)"
  sleep 10
fi

log "Papéis do deploy (projeto)"
for role in \
  roles/run.admin roles/pubsub.admin roles/datastore.owner roles/bigquery.admin \
  roles/secretmanager.admin roles/iam.serviceAccountAdmin roles/iam.serviceAccountUser \
  roles/resourcemanager.projectIamAdmin roles/monitoring.editor roles/iap.admin \
  roles/serviceusage.serviceUsageAdmin; do
  gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${DEPLOYER_EMAIL}" \
    --role="$role" --condition=None >/dev/null
done

log "Papéis do deploy (recursos)"
gc storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
  --member="serviceAccount:${DEPLOYER_EMAIL}" --role=roles/storage.objectAdmin >/dev/null
gc artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER_EMAIL}" --role=roles/artifactregistry.writer >/dev/null

log "Workload Identity Federation (somente ${GITHUB_REPO} @ refs/heads/main)"
if ! gc iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1; then
  gc iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
fi
CONDITION="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/main'"
if ! gc iam workload-identity-pools providers describe "$PROVIDER" --location=global \
  --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gc iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
    --workload-identity-pool="$POOL" --display-name="demo_antigravity (main)" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.environment=assertion.environment" \
    --attribute-condition="$CONDITION"
fi
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}"
gc iam service-accounts add-iam-policy-binding "$DEPLOYER_EMAIL" \
  --role=roles/iam.workloadIdentityUser --member="$PRINCIPAL" >/dev/null

log "Orçamento de US\$ ${BUDGET_USD} com alertas para ${ADMIN_EMAIL}"
if ! gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format='value(displayName)' </dev/null 2>/dev/null \
  | grep -qx "demo-antigravity"; then
  CHANNEL="$(gc beta monitoring channels create --display-name='Admin (orçamento)' --type=email \
    --channel-labels="email_address=${ADMIN_EMAIL}" --format='value(name)')"
  gcloud billing budgets create --quiet --billing-account="$BILLING_ACCOUNT" --display-name="demo-antigravity" \
    --budget-amount="${BUDGET_USD}USD" --filter-projects="projects/${PROJECT_ID}" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
    --notifications-rule-monitoring-notification-channels="$CHANNEL" </dev/null \
    || echo "AVISO: sem permissão na conta de faturamento; crie o orçamento pelo console."
fi

cat <<EOF

Bootstrap concluído. Configure no GitHub (Settings → Secrets and variables → Actions → Variables):
  GCP_PROJECT_ID       = ${PROJECT_ID}
  GCP_REGION           = ${REGION}
  GCP_WIF_PROVIDER     = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
  GCP_DEPLOY_SA        = ${DEPLOYER_EMAIL}
EOF
