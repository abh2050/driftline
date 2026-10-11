import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

// Console screenshots for docs/site (index.html), from real exported data:
//   uv run python -m scripts.export_console_fixture <incident id>
//   MOCK_DATA=$PWD/tests/fixtures/real.json SCREENSHOTS=1 npx playwright test screenshots
const OUT = path.resolve(__dirname, "../../../docs/site/screenshots");
const enabled = !!process.env.SCREENSHOTS && !!process.env.MOCK_DATA;
const data = enabled ? JSON.parse(readFileSync(process.env.MOCK_DATA!, "utf8")) : null;

test.describe("screenshots", () => {
  test.skip(!enabled, "set SCREENSHOTS=1 and MOCK_DATA to capture");
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

  test("incidents", async ({ page }) => {
    await page.goto("/incidents");
    await expect(page.getByTestId("incident-row").first()).toBeVisible();
    await page.screenshot({ path: `${OUT}/incidents.png` });
  });

  test("incident detail", async ({ page }) => {
    await page.goto(`/incidents/${data.incident_id}`);
    await expect(page.getByRole("figure", { name: "D3 scores" })).toBeVisible();
    await page.waitForTimeout(800); // let the charts finish drawing
    await page.screenshot({ path: `${OUT}/incident.png`, fullPage: true });
  });

  test("approvals", async ({ page }) => {
    await page.goto("/approvals");
    await expect(page.getByTestId("approval").first()).toBeVisible();
    await page.screenshot({ path: `${OUT}/approvals.png` });
  });

  test("router", async ({ page }) => {
    await page.goto("/router");
    await page.getByRole("tab", { name: "D3" }).click();
    await expect(page.getByRole("figure", { name: "Reliability diagram" })).toBeVisible();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/router.png`, fullPage: true });
  });

  test("chat", async ({ page }) => {
    await page.goto("/chat");
    await page.getByRole("textbox", { name: "Incident id (optional)" }).fill(data.incident_id);
    await page.getByRole("textbox", { name: "Question" }).fill(data.chat.question);
    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.getByRole("button", { name: "Ask" })).toBeEnabled({ timeout: 20_000 });
    await page.screenshot({ path: `${OUT}/chat.png`, fullPage: true });
  });

  test("demo", async ({ page }) => {
    await page.goto("/demo");
    await page.getByRole("button", { name: "Replay F3" }).click();
    await expect(page.getByRole("list", { name: "Demo log" })).toContainText("ok");
    await page.screenshot({ path: `${OUT}/demo.png` });
  });
});
