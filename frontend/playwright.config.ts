import { defineConfig } from "@playwright/test";
import path from "node:path";

const smokePort = Number(process.env.PLAYWRIGHT_PORT ?? 3012);
const smokeApiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 18002);
const smokeBaseUrl = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${smokePort}`;
const smokeApiBaseUrl = process.env.API_BASE_URL ?? `http://127.0.0.1:${smokeApiPort}`;
const smokeOperatorUser = process.env.PLAYWRIGHT_OPERATOR_USER ?? "operator";
const smokeOperatorPassword = process.env.PLAYWRIGHT_OPERATOR_PASSWORD ?? "playwright-operator-password";
const smokeAuthorization = `Basic ${Buffer.from(`${smokeOperatorUser}:${smokeOperatorPassword}`).toString("base64")}`;
const repoRoot = path.resolve(process.cwd(), "..");
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_SERVER === "1";
const portableNodeDir = path.join(repoRoot, ".tools", "node-v22.21.1-win-x64");
const shellQuote = (value: string) => `"${value}"`;
const pnpmCommand =
  process.platform === "win32"
    ? `${shellQuote(path.join(portableNodeDir, "corepack.cmd"))} pnpm`
    : "corepack pnpm";
const nodeCommand =
  process.platform === "win32" ? shellQuote(path.join(portableNodeDir, "node.exe")) : "node";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: smokeBaseUrl,
    extraHTTPHeaders: {
      Authorization: smokeAuthorization
    }
  },
  webServer: [
    {
      command: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\run_playwright_backend.ps1 -Port ${smokeApiPort}`,
      port: smokeApiPort,
      reuseExistingServer,
      cwd: repoRoot
    },
    {
      command: `${pnpmCommand} run build && ${nodeCommand} node_modules\\next\\dist\\bin\\next start -p ${smokePort}`,
      port: smokePort,
      reuseExistingServer,
      timeout: 180_000,
      cwd: process.cwd(),
      env: {
        APP_ENV: "development",
        API_BASE_URL: smokeApiBaseUrl,
        OPERATOR_UI_USERNAME: smokeOperatorUser,
        OPERATOR_UI_PASSWORD: smokeOperatorPassword,
        FRONTEND_AUTH_SECRET: "playwright-session-secret-with-more-than-thirty-two-chars",
        OPERATOR_API_KEY: smokeOperatorPassword
      }
    }
  ]
});
