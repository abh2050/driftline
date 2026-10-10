import { expect, test } from "@playwright/test";

const pages = [
  { path: "/incidents", heading: "Incidents" },
  { path: "/incidents/inc-123", heading: "Incident inc-123" },
  { path: "/approvals", heading: "Approvals" },
  { path: "/router", heading: "Router benchmark" },
  { path: "/chat", heading: "Chat" },
  { path: "/demo", heading: "Demo controls" },
];

for (const { path, heading } of pages) {
  test(`${path} renders`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  });
}

test("root redirects to incidents", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/incidents$/);
});

test("incident feed lists incidents and adds live ones", async ({ page }) => {
  await page.goto("/incidents");
  await expect(page.getByRole("link", { name: "inc-123" })).toBeVisible();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  // the mocked stream pushes inc-live after it connects
  await expect(page.getByRole("link", { name: "inc-live" })).toBeVisible();
  await expect(page.getByTestId("incident-row").first()).toContainText("inc-live");
});

test("incident detail shows routing, RCA, evals, guards and the PR", async ({ page }) => {
  await page.goto("/incidents/inc-123");
  await expect(page.getByRole("figure", { name: "D3 scores" })).toContainText("chose retrieval");
  await expect(page.getByRole("figure", { name: "D3 scores" })).toContainText("margin 0.80");
  await expect(page.getByText("top_k was lowered from 8 to 2")).toBeVisible();
  await expect(page.getByRole("table", { name: "Eval comparison" })).toContainText("recall_at_k");
  await expect(page.getByRole("figure", { name: "D5 scores" })).toBeVisible();
  await expect(page.getByRole("figure", { name: "D6 scores" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Pull request #7" })).toHaveAttribute(
    "href",
    "https://github.com/abh2050/driftline/pull/7",
  );
  await expect(page.getByRole("link", { name: "Trace in Application Insights" })).toBeVisible();
});

test("admin approves a runbook", async ({ page }) => {
  await page.goto("/approvals");
  const card = page.getByTestId("approval");
  await expect(card).toContainText("re-enable the indexer");
  await card.getByRole("button", { name: "Approve runbook" }).click();
  await expect(card.getByRole("status")).toContainText("approved");
});

test("router report shows metrics and a reliability diagram", async ({ page }) => {
  await page.goto("/router");
  await expect(page.getByRole("table", { name: "Router metrics" })).toContainText("gpt-5-mini");
  await page.getByRole("tab", { name: "D4" }).click();
  await expect(page.getByRole("tab", { name: "D4" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("figure", { name: "Reliability diagram" })).toBeVisible();
});

test("chat streams the explainer answer", async ({ page }) => {
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "Question" }).fill("Why retrieval?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByRole("list", { name: "Conversation" })).toContainText(
    "The incident was routed to retrieval because top_k changed.",
  );
});

test("admin can start a replay from the demo page", async ({ page }) => {
  await page.goto("/demo");
  await page.getByRole("button", { name: "Replay F3" }).click();
  await expect(page.getByRole("list", { name: "Demo log" })).toContainText("replay-demo-f3-abc123");
});

test.describe("as a viewer", () => {
  test.use({ extraHTTPHeaders: { "x-mock-roles": "viewer" } });

  test("cannot reach the demo controls", async ({ page }) => {
    await page.goto("/demo");
    await expect(page.getByRole("alert").filter({ hasText: "Admin role required" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Replay F3" })).toHaveCount(0);
  });
});
