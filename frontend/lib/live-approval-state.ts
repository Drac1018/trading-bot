export type LiveApprovalSource = {
  approval_armed?: boolean | null;
  approval_expires_at?: string | null;
  live_execution_armed?: boolean | null;
  live_execution_armed_until?: string | null;
  live_approval_window_minutes?: number | null;
};

function hasOwnApprovalField(source: LiveApprovalSource, key: keyof LiveApprovalSource) {
  return Object.prototype.hasOwnProperty.call(source, key);
}

export function resolveApprovalArmed(source: LiveApprovalSource): boolean {
  if (typeof source.approval_armed === "boolean") {
    return source.approval_armed;
  }
  return source.live_execution_armed ?? false;
}

export function resolveApprovalExpiresAt(source: LiveApprovalSource): string | null {
  if (hasOwnApprovalField(source, "approval_expires_at")) {
    return source.approval_expires_at ?? null;
  }
  if (hasOwnApprovalField(source, "approval_armed") && source.approval_armed !== true) {
    return null;
  }
  return source.live_execution_armed_until ?? null;
}

export function resolveUnlimitedApproval(source: LiveApprovalSource): boolean {
  return resolveApprovalArmed(source) && resolveApprovalExpiresAt(source) === null;
}
