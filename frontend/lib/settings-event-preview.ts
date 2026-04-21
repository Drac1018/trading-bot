import {
  describeSourceStatusHelp,
  inferEventSourceProvenance,
  summarizeEntryPolicy,
  summarizeReasonCodes,
  type EventOperatorControlPayload,
} from "./event-operator-control.js";

export type SettingsEventPreviewSummary = {
  entryPolicySummary: string;
  alignmentReasonSummary: string;
  eventSourceHelp: string;
};

export function buildSettingsEventPreviewSummary(
  eventOperatorControl: EventOperatorControlPayload | null | undefined,
): SettingsEventPreviewSummary {
  const eventContext = eventOperatorControl?.event_context;
  const provenance = inferEventSourceProvenance(eventContext);
  return {
    entryPolicySummary: summarizeEntryPolicy({
      effectivePolicyPreview: eventOperatorControl?.effective_policy_preview,
      blockedReason: eventOperatorControl?.blocked_reason,
      approvalRequiredReason: eventOperatorControl?.approval_required_reason,
      policySource: eventOperatorControl?.policy_source,
    }),
    alignmentReasonSummary: summarizeReasonCodes(eventOperatorControl?.alignment_decision?.reason_codes),
    eventSourceHelp: describeSourceStatusHelp(eventContext?.source_status, {
      kind: "event_context",
      provenance,
    }),
  };
}
