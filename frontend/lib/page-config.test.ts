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

test("dashboard page copy uses Korean scheduler and operator wording", async () => {
  const { dashboardPages } = await pageConfigModule;

  assert.equal(dashboardPages.scheduler.title, "스케줄러 상태");
  assert.equal(dashboardPages.scheduler.sections[0]?.title, "스케줄러 실행 기록");
  assert.doesNotMatch(dashboardPages.market.description, /feature|risk/);
  assert.equal(dashboardPages.orders.eyebrow, "주문 기록");
});
