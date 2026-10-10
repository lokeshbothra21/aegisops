import { defineConfig, devices } from "@playwright/test";

// Smoke tests against a production build, with the API mocked at the network layer
// (e2e/mock-api.ts), so CI needs no backend. NEXT_PUBLIC_API_URL is baked in at build time.
const PORT = 3100;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  webServer: {
    command: `npx next build && npx next start --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    env: { NEXT_PUBLIC_API_URL: "http://api.test" },
    reuseExistingServer: false,
    timeout: 240_000,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
