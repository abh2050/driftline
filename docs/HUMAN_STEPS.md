# Human steps

Steps Claude Code cannot do for you. Each one is added at the point the build is blocked, with its date.

## 2026-10-09: Azure sign in and Foundry model quota

> **Resolved by ADR 0001:** Decision-1 and Claude are dropped, so the quota requests in steps 3–4 are optional. Only step 1 (`azd auth login`) is still needed.

Subscription: `Azure subscription 1` (37dc8b49-69c9-4d42-827d-9727d47224c1), Pay-As-You-Go. Target region: **westus3**, where the portal created project `avixek-7316` (resource group `rg-avixek-3684`). Quota numbers below are the same in westus3 and eastus2.

1. Sign in to azd: `azd auth login`. `az login` is already done.
2. Quota check in westus3 (thousands of tokens per minute):
   | Need | Model | Deployment type | Current quota | Action |
   |---|---|---|---|---|
   | Router | Microsoft-Decision-1 | GlobalStandard | 0 | Request quota |
   | Specialists, Claude router | claude-sonnet-5-5 | GlobalStandard | 0 | Request quota |
   | General chat, LLM router | gpt-5.4-mini | DataZoneStandard | 200 | None |
   | Judge | o4-mini | GlobalStandard | 100 | None |
   | Embedding | text-embedding-3-small | GlobalStandard | 1000 | None |
3. Request quota for Microsoft-Decision-1 and claude-sonnet-5-5:
   - Go to https://ai.azure.com and open project `avixek-7316` (westus3).
   - The quota is counted in **requests per minute** (the unit is `Requests Per Minute - Microsoft-Decision-1`), and the limit is 0 in every region. Ask for **500 RPM** in **westus3**, GlobalStandard. A test deployment fails with: `InsufficientQuota ... the quota limit is 0 for quota Requests Per Minute - Microsoft-Decision-1`.
   - With the New Foundry toggle on, open **Operate → Quota** (or **Manage → Quota**), go to the Tokens per minute tab and click **Request quota** in the upper right. If it isn't there, use the form at https://aka.ms/oai/stuquotarequest. In the classic portal it's under Management center → Quota. Then and submit one request per model, choosing westus3 and GlobalStandard. 100K TPM is enough for this project.
   - If the portal does not offer a quota form for a model, open an Azure support request: Help + support → Create a support request → Service and subscription limits (quotas) → Microsoft Foundry / Cognitive Services. Name the model, the region and the GlobalStandard deployment type.
4. Accept the Claude Marketplace terms: in the Foundry model catalog, open claude-sonnet-5-5 and click **Deploy**. Fill in the industry, organization name and country. This creates the Marketplace subscription. If the deploy then fails on quota, wait for step 3 to be approved.
5. Tell Claude Code when the approval emails arrive. Approval can take several days. Phase 0 and the offline parts of Phase 1 can go ahead in the meantime.

## 2026-10-09: Rotate the leaked Foundry API key

The key for `avixek-7316-resource` was pasted into a chat session. Regenerate it:

    az cognitiveservices account keys regenerate -g rg-avixek-3684 -n avixek-7316-resource --key-name key1

Driftline does not use API keys. Every service signs in with managed identity through DefaultAzureCredential (spec section 2.4).

## 2026-10-09: Review at least 30 golden QA records (Phase 1 acceptance)

Run this in a terminal at the repo root:

    uv run python -m synth.review

For each record, check that the question is answerable from the source fact and that the reference answer is right. Press `a` to accept, `e` to edit the question, `r` to reject, `s` to skip and `q` to quit. Progress is saved after every record to `evals/datasets/golden_reviews.jsonl`. Check your count with:

    uv run python -m synth.review status

Tell Claude Code when `reviewed` is 30 or more.

> **2026-10-09:** The owner deferred this review and asked to continue to Phase 2. Phase 1 is accepted with this item **open**. Complete it before relying on layer 1 numbers in reports.

## 2026-10-10: Create the Driftline GitHub App (Phase 6)

The agents open pull requests through a GitHub App (not a personal token), with access to this repository only.

1. Go to https://github.com/settings/apps/new and fill in:
   - **GitHub App name:** `driftline-abh2050` (names are global; add a suffix if it's taken)
   - **Homepage URL:** `https://github.com/abh2050/driftline`
   - **Webhook:** untick **Active**
   - **Repository permissions:** Contents **Read and write**, Pull requests **Read and write**, Metadata **Read-only** (set automatically). Leave everything else at No access.
   - **Where can this GitHub App be installed?** Only on this account
   - Click **Create GitHub App**.
2. On the app page, note the **App ID**. Under **Private keys**, click **Generate a private key**, which downloads a `.pem` file.
3. In the left menu, click **Install App**, then **Install** next to your account, choose **Only select repositories**, pick `driftline` and click **Install**. The URL then ends in `/installations/<number>`. That number is the **installation ID**.
4. Store the key in Key Vault (replace the path), then delete the local file:

       az keyvault secret set --vault-name dl-dev-kv-rf5tbsjvwdzcs --name github-app-private-key --file ~/Downloads/<your-app>.private-key.pem -o none
       rm ~/Downloads/<your-app>.private-key.pem

5. Tell Claude Code the **App ID** and **installation ID**. They are not secrets, but never paste the key itself.
