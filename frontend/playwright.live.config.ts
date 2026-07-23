import { defineConfig, devices } from "@playwright/test";

const postgresDsn = process.env.OMNICELL_TEST_POSTGRES_DSN?.trim();
if (!postgresDsn) {
  throw new Error("运行 live E2E 前必须设置 OMNICELL_TEST_POSTGRES_DSN");
}

function port(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65_535) {
    throw new Error(`${name} 不是合法端口`);
  }
  return value;
}

const webPort = port("OMNICELL_LIVE_WEB_PORT", 14_173);
const webUrl = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./tests/e2e-live",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: webUrl,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-live",
      use: {
        ...devices["Desktop Chrome"],
        channel: process.env.OMNICELL_PLAYWRIGHT_BROWSER_CHANNEL,
      },
    },
  ],
});
