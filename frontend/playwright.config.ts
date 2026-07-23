import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // The suite is small. Serial execution keeps results deterministic.
  workers: 1,
  reporter: "html",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      // 默认使用 Playwright 隔离管理的 Chromium，避免测试进程触发或污染
      // 用户日常使用的系统 Chrome。确需验证系统 channel 时再显式设置。
      use: {
        ...devices["Desktop Chrome"],
        channel: process.env.OMNICELL_PLAYWRIGHT_BROWSER_CHANNEL,
      },
    },
  ],
  webServer: {
    command: "npm run preview -- --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
});
