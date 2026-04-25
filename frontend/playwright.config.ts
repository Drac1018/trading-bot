import { defineConfig } from "@playwright/test";
import path from "node:path";

const smokePort = Number(process.env.PLAYWRIGHT_PORT ?? 3012);
const smokeApiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 18002);
const smokeBaseUrl = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${smokePort}`;
const smokeApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? `http://127.0.0.1:${smokeApiPort}`;
const repoRoot = path.resolve(process.cwd(), "..");
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_SERVER === "1";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: smokeBaseUrl
  },
  webServer: [
    {
      command: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\run_playwright_backend.ps1 -Port ${smokeApiPort}`,
      port: smokeApiPort,
      reuseExistingServer,
      cwd: repoRoot
    },
    {
      command: `npm run build && npm run start -- -p ${smokePort}`,
      port: smokePort,
      reuseExistingServer,
      cwd: process.cwd(),
      env: {
        API_BASE_URL: smokeApiBaseUrl,
        NEXT_PUBLIC_API_BASE_URL: smokeApiBaseUrl
      }
    }
  ]
});
