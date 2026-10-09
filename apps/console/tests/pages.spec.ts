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
