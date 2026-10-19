#!/usr/bin/env bash
# Provision the Site Ops Console on Azure and wire GitHub Actions to deploy it.
#
#   infra/deploy.sh --what-if   # preview only: lists what would be created, creates nothing
#   infra/deploy.sh             # create or update everything, then set GitHub variables/secrets
#
# Run from the repository root after `az login` and `gh auth login`. Everything secret is
# typed here or generated here and goes straight to Azure or GitHub; nothing is printed or
# written to the repository. The database has no password at all (Entra auth only).
set -euo pipefail

LOCATION="newzealandnorth"   # see infra/main.bicep for why not Australia
RESOURCE_GROUP="rg-siteops-nzn"
MODE="${1:-deploy}"

command -v az >/dev/null || { echo "Azure CLI (az) is required."; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required."; exit 1; }
[ -f infra/main.bicep ] || { echo "Run this from the repository root."; exit 1; }

subscription_name="$(az account show --query name -o tsv)"
subscription_id="$(az account show --query id -o tsv)"
tenant_id="$(az account show --query tenantId -o tsv)"
admin_oid="$(az ad signed-in-user show --query id -o tsv)"
admin_upn="$(az ad signed-in-user show --query userPrincipalName -o tsv)"
repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

echo "Subscription:   $subscription_name"
echo "Region:         $LOCATION"
echo "Resource group: $RESOURCE_GROUP"
echo "DB admin:       $admin_upn"
echo "GitHub repo:    $repo"
echo

read -rp "Email address for ingestion alerts: " alert_email
[ -n "$alert_email" ] || { echo "An alert email is required."; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
chmod 700 "$workdir"

# Reuse the Better Auth secret of an existing deployment, so sessions survive re-runs.
auth_secret="$(az webapp list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null \
  | xargs -I{} az webapp config appsettings list -g "$RESOURCE_GROUP" -n {} \
      --query "[?name=='BETTER_AUTH_SECRET'].value | [0]" -o tsv 2>/dev/null || true)"
if [ -z "$auth_secret" ] || [ "$MODE" = "--what-if" ]; then
  auth_secret="$(openssl rand -base64 48 | tr -d '\n')"
fi

python3 - "$workdir/params.json" <<PY
import json, sys
json.dump({
  "\$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "location": {"value": "$LOCATION"},
    "resourceGroupName": {"value": "$RESOURCE_GROUP"},
    "adminObjectId": {"value": "$admin_oid"},
    "adminPrincipalName": {"value": "$admin_upn"},
    "alertEmail": {"value": "$alert_email"},
    "githubRepository": {"value": "$repo"},
    "betterAuthSecret": {"value": "$auth_secret"},
  },
}, open(sys.argv[1], "w"))
PY
chmod 600 "$workdir/params.json"
unset auth_secret

if [ "$MODE" = "--what-if" ]; then
  az deployment sub what-if -l "$LOCATION" -f infra/main.bicep -p "@$workdir/params.json"
  exit 0
fi

read -rp "Create or update these Azure resources now? Type yes to continue: " answer
[ "$answer" = "yes" ] || { echo "Stopped. Nothing was created."; exit 0; }

echo "Registering resource providers (one-time, free)..."
for provider in Microsoft.Web Microsoft.DBforPostgreSQL Microsoft.Insights Microsoft.OperationalInsights \
  Microsoft.ManagedIdentity Microsoft.AlertsManagement; do
  az provider register -n "$provider" --wait --output none
done

deployment="siteops-$(date +%Y%m%d%H%M%S)"
echo "Deploying $deployment (PostgreSQL takes 5-10 minutes)..."
az deployment sub create -l "$LOCATION" -n "$deployment" -f infra/main.bicep -p "@$workdir/params.json" --output none

out() { az deployment sub show -n "$deployment" --query "properties.outputs.$1.value" -o tsv; }

echo "Setting GitHub Actions variables (names and IDs, not secrets)..."
gh variable set AZURE_CLIENT_ID --body "$(out deployIdentityClientId)"
gh variable set AZURE_TENANT_ID --body "$tenant_id"
gh variable set AZURE_SUBSCRIPTION_ID --body "$subscription_id"
gh variable set AZURE_RESOURCE_GROUP --body "$(out resourceGroupName)"
gh variable set AZURE_WEBAPP_NAME --body "$(out webAppName)"
gh variable set AZURE_WEBAPP_PRINCIPAL_ID --body "$(out webAppPrincipalId)"
gh variable set AZURE_PG_SERVER --body "$(out postgresServerName)"
gh variable set AZURE_PG_HOST --body "$(out postgresHost)"
gh variable set AZURE_DEPLOY_IDENTITY_NAME --body "$(out deployIdentityName)"
gh variable set AZURE_APPINSIGHTS_NAME --body "$(out appInsightsName)"

read -rp "Console admin sign-in email [admin@example.com]: " admin_email
gh variable set SEED_ADMIN_EMAIL --body "${admin_email:-admin@example.com}"
read -rsp "Console admin password (12+ characters, not shown): " admin_password; echo
[ "${#admin_password}" -ge 12 ] || { echo "The password must be at least 12 characters."; exit 1; }
printf '%s' "$admin_password" | gh secret set SEED_ADMIN_PASSWORD
unset admin_password

read -rp "Also create a read-only viewer account for reviewers? [y/N]: " want_viewer
if [ "$want_viewer" = "y" ] || [ "$want_viewer" = "Y" ]; then
  read -rp "Viewer sign-in email [viewer@example.com]: " viewer_email
  gh variable set SEED_VIEWER_EMAIL --body "${viewer_email:-viewer@example.com}"
  read -rsp "Viewer password (12+ characters, not shown): " viewer_password; echo
  [ "${#viewer_password}" -ge 12 ] || { echo "The password must be at least 12 characters."; exit 1; }
  printf '%s' "$viewer_password" | gh secret set SEED_VIEWER_PASSWORD
  unset viewer_password
fi

echo
echo "Done. Web app: $(out webAppUrl)"
echo "Push to ops-console (or main) to run .github/workflows/deploy.yml."
