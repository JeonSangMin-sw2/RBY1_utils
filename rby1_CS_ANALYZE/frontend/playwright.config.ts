import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4173";
const appRoot = path.basename(process.cwd()) === "frontend"
  ? path.resolve(process.cwd(), "..")
  : process.cwd();
const isolatedDataRoot = path.join("/tmp", `rby1-cs-analyzer-playwright-${process.pid}`);

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    command: `python -m rby1_analyzer.launcher --port 4173 --no-open-browser --bootstrap-token e2e-proof --data-root ${isolatedDataRoot}`,
    cwd: appRoot,
    env: { PYTHONPATH: path.join(appRoot, "src") },
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    {
      name: "chromium",
      grepInvert: /@network-off/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "network-off",
      grep: /@network-off/,
      use: { ...devices["Desktop Chrome"], serviceWorkers: "block" },
    },
  ],
});
