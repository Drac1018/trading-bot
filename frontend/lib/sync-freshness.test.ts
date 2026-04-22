import assert from "node:assert/strict";
import test from "node:test";

type SyncFreshnessModule = typeof import("./sync-freshness");

const syncFreshnessModule = import(
  new URL("./sync-freshness.ts", import.meta.url).href,
) as Promise<SyncFreshnessModule>;

test("sync freshness keeps unknown state distinct from stale fallback", async () => {
  const { getSyncScopeBadge, translateSyncScopeStatus } = await syncFreshnessModule;

  const scope = {
    status: "unknown",
    stale: true,
  };

  assert.deepEqual(getSyncScopeBadge(scope), {
    label: "확인 필요",
    kind: "warn",
  });
  assert.equal(translateSyncScopeStatus(scope), "확인 필요");
});

test("sync freshness surfaces failed and skipped states explicitly", async () => {
  const { getSyncScopeBadge, getSyncScopeReason, translateSyncScopeStatus } = await syncFreshnessModule;

  const failedScope = {
    status: "failed",
    stale: false,
    last_failure_reason: "ACCOUNT_STATE_STALE",
  };
  const skippedScope = {
    status: "skipped",
    stale: false,
    last_skip_reason: "MANUAL_USER_REQUEST",
  };

  assert.deepEqual(getSyncScopeBadge(failedScope), {
    label: "동기화 실패",
    kind: "danger",
  });
  assert.equal(translateSyncScopeStatus(failedScope), "동기화 실패");
  assert.deepEqual(getSyncScopeReason(failedScope), {
    label: "실패 사유",
    reasonCode: "ACCOUNT_STATE_STALE",
  });

  assert.deepEqual(getSyncScopeBadge(skippedScope), {
    label: "동기화 보류",
    kind: "warn",
  });
  assert.equal(translateSyncScopeStatus(skippedScope), "동기화 보류");
  assert.deepEqual(getSyncScopeReason(skippedScope), {
    label: "보류 사유",
    reasonCode: "MANUAL_USER_REQUEST",
  });
});

test("sync freshness still maps stale and synced states for the overview card", async () => {
  const { getSyncScopeBadge, getSyncScopeReason, translateSyncScopeStatus } = await syncFreshnessModule;

  assert.deepEqual(
    getSyncScopeBadge({
      status: "stale",
      stale: true,
    }),
    {
      label: "지연",
      kind: "warn",
    },
  );
  assert.equal(
    translateSyncScopeStatus({
      status: "synced",
      stale: false,
      incomplete: false,
    }),
    "정상",
  );
  assert.deepEqual(
    getSyncScopeReason({
      status: "synced",
      last_failure_reason: "OPEN_ORDERS_STATE_STALE",
    }),
    {
      label: "마지막 실패",
      reasonCode: "OPEN_ORDERS_STATE_STALE",
    },
  );
});
