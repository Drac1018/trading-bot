import { mkdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const nextServerPath = resolve(process.cwd(), ".next", "server");
const generatedPaths = [
  resolve(process.cwd(), ".next", "BUILD_ID"),
  resolve(process.cwd(), ".next", "build"),
  resolve(process.cwd(), ".next", "cache", "webpack"),
  resolve(process.cwd(), ".next", "diagnostics"),
  resolve(process.cwd(), ".next", "lock"),
  resolve(process.cwd(), ".next", "package.json"),
  resolve(process.cwd(), ".next", "server"),
  resolve(process.cwd(), ".next", "standalone"),
  resolve(process.cwd(), ".next", "static"),
  resolve(process.cwd(), ".next", "trace"),
  resolve(process.cwd(), ".next", "types"),
  resolve(process.cwd(), ".next", "turbopack"),
];

for (const generatedPath of generatedPaths) {
  rmSync(generatedPath, { force: true, recursive: true });
}

mkdirSync(nextServerPath, { recursive: true });
