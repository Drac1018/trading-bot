import assert from "node:assert/strict";
import test from "node:test";

type LiveApprovalStateModule = typeof import("./live-approval-state");

const liveApprovalStateModule = import(
  new URL("./live-approval-state.ts", import.meta.url).href,
) as Promise<LiveApprovalStateModule>;

test("resolveApprovalArmed prefers boolean approval publication over legacy live arm state", async () => {
  const { resolveApprovalArmed, resolveApprovalExpiresAt } = await liveApprovalStateModule;

  assert.equal(
    resolveApprovalArmed({
      approval_armed: false,
      live_execution_armed: true,
    }),
    false,
  );
  assert.equal(
    resolveApprovalArmed({
      approval_armed: null,
      live_execution_armed: true,
    }),
    true,
  );
  assert.equal(
    resolveApprovalExpiresAt({
      approval_armed: false,
      approval_expires_at: null,
      live_execution_armed_until: "2026-05-28T04:00:00Z",
    }),
    null,
  );
});

test("resolveApprovalArmed falls back to legacy live arm state for stale settings payloads", async () => {
  const { resolveApprovalArmed } = await liveApprovalStateModule;

  assert.equal(resolveApprovalArmed({ live_execution_armed: true }), true);
  assert.equal(resolveApprovalArmed({ live_execution_armed: false }), false);
});

test("resolveApprovalExpiresAt prefers explicit approval expiry over legacy arm expiry", async () => {
  const { resolveApprovalExpiresAt } = await liveApprovalStateModule;

  assert.equal(
    resolveApprovalExpiresAt({
      approval_expires_at: "2026-05-28T03:00:00Z",
      live_execution_armed_until: "2026-05-28T04:00:00Z",
    }),
    "2026-05-28T03:00:00Z",
  );
  assert.equal(
    resolveApprovalExpiresAt({
      approval_armed: false,
      live_execution_armed_until: "2026-05-28T04:00:00Z",
    }),
    null,
  );
});

test("resolveUnlimitedApproval requires an open approval with no expiry", async () => {
  const { resolveUnlimitedApproval } = await liveApprovalStateModule;

  assert.equal(
    resolveUnlimitedApproval({
      approval_armed: true,
      approval_expires_at: null,
      live_approval_window_minutes: 180,
    }),
    true,
  );
  assert.equal(
    resolveUnlimitedApproval({
      approval_armed: false,
      approval_expires_at: null,
      live_approval_window_minutes: 0,
    }),
    false,
  );
});
