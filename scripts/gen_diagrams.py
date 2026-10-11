"""Architecture diagrams with Azure icons (azure-diagrams skill: the `diagrams` library and
Graphviz). Writes docs/site/diagrams/*.png.

uv run --no-project --with diagrams==0.25.1 python scripts/gen_diagrams.py
"""

from __future__ import annotations

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.azure.compute import ContainerApps
from diagrams.azure.database import CosmosDb
from diagrams.azure.identity import ActiveDirectory, ManagedIdentities
from diagrams.azure.integration import APIManagement, ServiceBus
from diagrams.azure.ml import AzureOpenAI, CognitiveServices
from diagrams.azure.monitor import ApplicationInsights, LogAnalyticsWorkspaces
from diagrams.azure.security import KeyVaults
from diagrams.azure.storage import BlobStorage
from diagrams.azure.web import Search
from diagrams.onprem.client import Users
from diagrams.onprem.vcs import Github

OUT = Path(__file__).resolve().parents[1] / "docs" / "site" / "diagrams"
GRAPH = {
    "pad": "0.4",
    "nodesep": "0.5",
    "ranksep": "1.0",
    "fontsize": "22",
    "splines": "spline",
    "concentrate": "true",
}
NODE = {"fontsize": "13"}
BLUE, GREY, RED, GREEN = "#2563eb", "#64748b", "#dc2626", "#16a34a"


def architecture() -> None:
    with Diagram(
        "Driftline on Azure",
        filename=str(OUT / "architecture"),
        show=False,
        direction="LR",
        graph_attr=GRAPH,
        node_attr=NODE,
    ):
        people = Users("engineers")
        with Cluster("Monitored application"):
            traffic = ContainerApps("traffic-gen\n(hourly job)")
            rag = ContainerApps("patient-rag")
            search = Search("AI Search")
            replay = ContainerApps("replay\n(1440x job)")
        with Cluster("Messaging and state"):
            bus = ServiceBus("Service Bus\ntopics")
            cosmos = CosmosDb("Cosmos DB\nserverless")
            audit = BlobStorage("audit\n(write-once)")
        with Cluster("Driftline"):
            ingest = ContainerApps("ingest")
            detector = ContainerApps("detector\nEWMA + CUSUM")
            router = ContainerApps("router\nD1-D6")
            orch = ContainerApps("orchestrator\nAgent Framework")
            sandbox = ContainerApps("sandbox-runner\n(event job)")
        with Cluster("Foundry"):
            models = AzureOpenAI("gpt-5-mini\ngpt-4.1-mini\ngpt-5.4-mini")
            shield = CognitiveServices("Content Safety\nprompt shields")
        with Cluster("MCP gateway"):
            apim = APIManagement("API Management\nper-agent policy")
            ghmcp = ContainerApps("github-mcp")
            kql = ContainerApps("kql-tool")
        github = Github("GitHub\nfix PRs")
        with Cluster("People"):
            console = ContainerApps("console\n(Entra sign-in)")
            ops = ContainerApps("ops-api")
        with Cluster("Observability"):
            otel = ContainerApps("otel-collector")
            logs = LogAnalyticsWorkspaces("Log Analytics\n+ workbook")
            appi = ApplicationInsights("App Insights")

        traffic >> rag >> search
        rag >> Edge(label="telemetry") >> bus
        replay >> bus
        bus >> ingest >> cosmos
        detector >> cosmos
        detector >> Edge(label="D1", color=BLUE) >> router
        detector >> Edge(label="incident") >> bus
        bus >> orch
        orch >> Edge(label="D2-D6", color=BLUE) >> router >> models
        orch >> Edge(label="agent identities", color=RED) >> apim
        apim >> ghmcp >> Edge(label="GitHub App") >> github
        apim >> kql >> logs
        orch >> shield
        orch >> Edge(label="sandbox", style="dashed") >> sandbox >> models
        orch >> audit
        people >> console >> ops
        ops >> Edge(label="approvals") >> bus
        # every service exports OTLP to the collector; one edge stands for all of them
        orch >> Edge(label="OTLP (all services)", color=GREY, style="dotted") >> otel
        otel >> logs
        logs - appi


def incident_flow() -> None:
    with Diagram(
        "One incident, end to end",
        filename=str(OUT / "incident-flow"),
        show=False,
        direction="LR",
        graph_attr={**GRAPH, "ranksep": "0.6"},
        node_attr=NODE,
    ):
        rag = ContainerApps("patient-rag\ntelemetry")
        detector = ContainerApps("detector\nCUSUM breach")
        d1 = ContainerApps("D1\nreal or noise")
        d23 = ContainerApps("D2 severity\nD3 specialist")
        spec = AzureOpenAI("specialist agent\nRCA via MCP")
        d4 = ContainerApps("D4\npr / runbook / escalate")
        with Cluster("pr"):
            sandbox = ContainerApps("sandbox\nlayer 1 on the fix")
            d5 = ContainerApps("D5 accept")
            d6 = ContainerApps("D6 before\nevery write")
            pr = Github("pull request\n(human review)")
        with Cluster("runbook / escalate"):
            gate = Users("approvals\n(human gate)")
            resume = ContainerApps("resume from\ncheckpoint")
        rag >> detector >> d1 >> d23 >> spec >> d4
        d4 >> Edge(color=GREEN) >> sandbox >> d5 >> d6 >> pr
        d4 >> Edge(color=RED) >> gate >> resume
        d5 >> Edge(label="gate failed", style="dashed", color=RED) >> gate


def security() -> None:
    with Diagram(
        "Who can write to the repository",
        filename=str(OUT / "security"),
        show=False,
        direction="LR",
        graph_attr={**GRAPH, "ranksep": "0.7"},
        node_attr=NODE,
    ):
        with Cluster("7 agent identities, one role each"):
            agents = [ManagedIdentities(n) for n in ("retrieval", "index", "pr-writer")]
        shield = CognitiveServices("1. prompt shields\non tool results")
        d6 = ContainerApps("2. D6: the router\njudges each write")
        apim = APIManagement("3. APIM policy\nallowlist, no merge,\nno push to main")
        proxy = ContainerApps("github-mcp\n(APIM identity only)")
        gh = Github("4. branch protection\nhuman review")
        kv = KeyVaults("Key Vault\nGitHub App key")
        for agent in agents:
            agent >> shield
        shield >> d6 >> apim >> proxy >> gh
        kv >> Edge(label="read by one identity", style="dashed") >> proxy
        with Cluster("People"):
            users = Users("engineers")
            entra = ActiveDirectory("Entra ID\nviewer / admin")
            ops = ContainerApps("ops-api\nadmin checked again")
            users >> entra >> ops


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    incident_flow()
    security()
    print(f"wrote {', '.join(sorted(p.name for p in OUT.glob('*.png')))}")
