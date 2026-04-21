import assert from "node:assert/strict";
import test from "node:test";

type PageConfigModule = typeof import("./page-config");

const pageConfigModule = import(
  new URL("./page-config.ts", import.meta.url).href,
) as Promise<PageConfigModule>;

test("normalizeSettingsView keeps supported deep-link values only", async () => {
  const { normalizeSettingsView } = await pageConfigModule;

  assert.equal(normalizeSettingsView("control"), "control");
  assert.equal(normalizeSettingsView("integration"), "integration");
  assert.equal(normalizeSettingsView("unexpected"), "control");
  assert.equal(normalizeSettingsView(null), "control");
});
