import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PORT ?? 3100);
const mockPort = Number(process.env.MOCK_OPS_PORT ?? 3199);

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://localhost:${port}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `node tests/mock-ops.mjs`,
      url: `http://localhost:${mockPort}/healthz`,
      env: { MOCK_OPS_PORT: String(mockPort) },
      reuseExistingServer: !process.env.CI,
    },
    {
      // all six pages run against the mocked ops-api (spec 10.2); roles come from a test header
      command: `npm run build && npm run start -- -p ${port}`,
      url: `http://localhost:${port}/incidents`,
      env: {
        OPS_API_URL: `http://localhost:${mockPort}`,
        CONSOLE_AUTH_MODE: "mock",
        CONSOLE_MOCK_ROLES: "admin",
        NEXT_PUBLIC_BROWSER_TRACES: "off",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  ],
});
