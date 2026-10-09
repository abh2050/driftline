"""List every Driftline resource in the azd resource group and check nothing is missing.

Usage: uv run python scripts/smoke.py   (reads AZURE_SUBSCRIPTION_ID / AZURE_RESOURCE_GROUP
from the environment or from `azd env get-values`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter

from azure.identity import AzureCliCredential
from azure.mgmt.resource.resources import ResourceManagementClient

EXPECTED = {
    "Microsoft.ManagedIdentity/userAssignedIdentities": 13,
    "Microsoft.OperationalInsights/workspaces": 1,
    "Microsoft.Insights/components": 1,
    "Microsoft.Monitor/accounts": 1,
    "Microsoft.Insights/dataCollectionEndpoints": 1,
    "Microsoft.Insights/dataCollectionRules": 2,
    "Microsoft.ContainerRegistry/registries": 1,
    "Microsoft.DocumentDB/databaseAccounts": 1,
    "Microsoft.ServiceBus/namespaces": 1,
    "Microsoft.Storage/storageAccounts": 1,
    "Microsoft.KeyVault/vaults": 1,
    "Microsoft.Search/searchServices": 1,
    "Microsoft.CognitiveServices/accounts": 2,
    "Microsoft.CognitiveServices/accounts/projects": 1,
    "Microsoft.ApiManagement/service": 1,
    "Microsoft.App/managedEnvironments": 1,
    "Microsoft.App/containerApps": 1,
}


def azd_env() -> dict[str, str]:
    try:
        out = subprocess.run(
            ["azd", "env", "get-values"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    pairs = (line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {k: v.strip('"') for k, v in pairs}


def main() -> int:
    env = {**azd_env(), **os.environ}
    sub, rg = env["AZURE_SUBSCRIPTION_ID"], env["AZURE_RESOURCE_GROUP"]
    client = ResourceManagementClient(AzureCliCredential(), sub)
    resources = sorted(client.resources.list_by_resource_group(rg), key=lambda r: (r.type, r.name))
    counts = Counter(str(r.type) for r in resources)
    print(f"{len(resources)} resources in {rg}")
    for r in resources:
        print(f"  {r.type:<55} {r.name}")
    missing = {t: n for t, n in EXPECTED.items() if counts.get(t, 0) < n}
    for t, n in missing.items():
        print(f"MISSING {t}: expected {n}, found {counts.get(t, 0)}")
    print("smoke: OK" if not missing else "smoke: FAILED")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
