"use client";

import {
  Field,
  InlineFeedback,
  StatusPill,
  Toggle,
  inputClass,
  type FeedbackMessage,
} from "./form-primitives";
import { formatDisplayValue } from "../../lib/ui-copy";

export type ExecutionRiskProfileDefinition = {
  profile_id: string;
  severity: number;
  label: string;
  description: string;
  new_entry_policy: string;
  blocks_new_entry_when_active: boolean;
};

export type ExecutionRiskProfilePolicySettings = {
  advisor_enabled: boolean;
  advisor_shadow_mode: boolean;
  auto_apply_mode: "off" | "shadow" | "conservative_only" | "manual_approval";
  normal_interval_seconds: number;
  elevated_interval_seconds: number;
  min_recheck_interval_seconds: number;
  recommendation_ttl_seconds: number;
  min_confidence_to_apply: number;
  relax_requires_consecutive_confirmations: number;
  min_profile_dwell_seconds: number;
};

export type ExecutionRiskProfileSettings = ExecutionRiskProfilePolicySettings & {
  blocking_severity: number;
  allowed_profiles: ExecutionRiskProfileDefinition[];
  allowed_new_entry_policies: Array<{ policy_id: string; description: string }>;
  current_state?: {
    deterministic_profile?: string | null;
    ai_recommended_profile?: string | null;
    final_active_profile?: string | null;
    shadow_final_profile?: string | null;
    selection_mode?: string | null;
    selected_reason?: string | null;
    active_profile_blocks_new_entry?: boolean | null;
    was_tightened_by_ai?: boolean | null;
    was_relaxation_blocked?: boolean | null;
    ai_recommendation_status?: string | null;
    ai_recommendation_id?: string | null;
    ai_recommendation_confidence?: number | null;
    ai_recommendation_valid_until?: string | null;
  };
  operation?: {
    advisor_runs_during_decision_cycle?: boolean;
    selector_runs_during_risk_guard?: boolean;
    manual_cycle_endpoint?: string;
    manual_cycle_can_reach_live_execution?: boolean;
    survival_paths_remain_allowed?: boolean;
  };
};

export const defaultExecutionRiskProfilePolicy: ExecutionRiskProfilePolicySettings = {
  advisor_enabled: true,
  advisor_shadow_mode: true,
  auto_apply_mode: "shadow",
  normal_interval_seconds: 900,
  elevated_interval_seconds: 900,
  min_recheck_interval_seconds: 900,
  recommendation_ttl_seconds: 900,
  min_confidence_to_apply: 0.7,
  relax_requires_consecutive_confirmations: 2,
  min_profile_dwell_seconds: 900,
};

const fallbackProfiles: ExecutionRiskProfileDefinition[] = [
  {
    profile_id: "NORMAL",
    severity: 0,
    label: "Normal",
    description: "기본 시장 상태입니다.",
    new_entry_policy: "일반 신규 진입 검토 가능",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "CAUTION",
    severity: 1,
    label: "Caution",
    description: "불확실성이 올라간 상태입니다.",
    new_entry_policy: "risk_guard 최종 검증 후 진입 가능",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "HIGH_VOLATILITY",
    severity: 2,
    label: "High volatility",
    description: "변동성 확대 또는 range break 상태입니다.",
    new_entry_policy: "더 보수적인 확인 상태",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "THIN_LIQUIDITY",
    severity: 2,
    label: "Thin liquidity",
    description: "스프레드/유동성 상태가 불리합니다.",
    new_entry_policy: "더 보수적인 확인 상태",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "STRESS",
    severity: 3,
    label: "Stress",
    description: "시장/운영 리스크가 높은 상태입니다.",
    new_entry_policy: "적용 모드에서 신규 진입 차단",
    blocks_new_entry_when_active: true,
  },
  {
    profile_id: "DEGRADED",
    severity: 4,
    label: "Degraded",
    description: "데이터, 동기화, 보호 주문 hard condition 상태입니다.",
    new_entry_policy: "신규 진입 차단, reduce/exit 경로는 분리",
    blocks_new_entry_when_active: true,
  },
];

const autoApplyModeOptions: Array<{
  value: ExecutionRiskProfilePolicySettings["auto_apply_mode"];
  label: string;
}> = [
  { value: "off", label: "off" },
  { value: "shadow", label: "shadow" },
  { value: "conservative_only", label: "conservative only" },
  { value: "manual_approval", label: "manual approval" },
];

export function executionRiskProfilePolicyFromSettings(
  settings?: ExecutionRiskProfileSettings | null,
): ExecutionRiskProfilePolicySettings {
  return {
    advisor_enabled: settings?.advisor_enabled ?? defaultExecutionRiskProfilePolicy.advisor_enabled,
    advisor_shadow_mode: settings?.advisor_shadow_mode ?? defaultExecutionRiskProfilePolicy.advisor_shadow_mode,
    auto_apply_mode: isAutoApplyMode(settings?.auto_apply_mode)
      ? settings.auto_apply_mode
      : defaultExecutionRiskProfilePolicy.auto_apply_mode,
    normal_interval_seconds:
      settings?.normal_interval_seconds ?? defaultExecutionRiskProfilePolicy.normal_interval_seconds,
    elevated_interval_seconds:
      settings?.elevated_interval_seconds ?? defaultExecutionRiskProfilePolicy.elevated_interval_seconds,
    min_recheck_interval_seconds:
      settings?.min_recheck_interval_seconds ?? defaultExecutionRiskProfilePolicy.min_recheck_interval_seconds,
    recommendation_ttl_seconds:
      settings?.recommendation_ttl_seconds ?? defaultExecutionRiskProfilePolicy.recommendation_ttl_seconds,
    min_confidence_to_apply:
      settings?.min_confidence_to_apply ?? defaultExecutionRiskProfilePolicy.min_confidence_to_apply,
    relax_requires_consecutive_confirmations:
      settings?.relax_requires_consecutive_confirmations ??
      defaultExecutionRiskProfilePolicy.relax_requires_consecutive_confirmations,
    min_profile_dwell_seconds:
      settings?.min_profile_dwell_seconds ?? defaultExecutionRiskProfilePolicy.min_profile_dwell_seconds,
  };
}

function isAutoApplyMode(value: unknown): value is ExecutionRiskProfilePolicySettings["auto_apply_mode"] {
  return autoApplyModeOptions.some((option) => option.value === value);
}

function secondsLabel(value: number | null | undefined) {
  if (!Number.isFinite(value ?? NaN)) {
    return "-";
  }
  const seconds = Number(value);
  if (seconds >= 60 && seconds % 60 === 0) {
    return `${seconds / 60}분`;
  }
  return `${seconds}초`;
}

function secondsToMinutes(value: number | null | undefined) {
  if (!Number.isFinite(value ?? NaN)) {
    return 0;
  }
  return Number(value) / 60;
}

function minutesToSeconds(value: string, minimumMinutes: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return minimumMinutes * 60;
  }
  return Math.max(Math.trunc(parsed), minimumMinutes) * 60;
}

function percentLabel(value: number | null | undefined) {
  if (!Number.isFinite(value ?? NaN)) {
    return "-";
  }
  return `${Math.round(Number(value) * 100)}%`;
}

function profileTone(profile: ExecutionRiskProfileDefinition) {
  if (profile.blocks_new_entry_when_active) {
    return "danger" as const;
  }
  if (profile.severity >= 2) {
    return "warn" as const;
  }
  if (profile.severity === 1) {
    return "neutral" as const;
  }
  return "good" as const;
}

export function ExecutionProfileSettingsPanel({
  settings,
  form,
  isPending,
  feedback,
  onPolicyChange,
  onSave,
}: {
  settings?: ExecutionRiskProfileSettings | null;
  form: ExecutionRiskProfilePolicySettings;
  isPending: boolean;
  feedback?: FeedbackMessage;
  onPolicyChange: (next: ExecutionRiskProfilePolicySettings) => void;
  onSave: () => void;
}) {
  const profiles = settings?.allowed_profiles?.length ? settings.allowed_profiles : fallbackProfiles;
  const current = settings?.current_state ?? {};
  const operation = settings?.operation ?? {};
  const canLiveTrigger = operation.manual_cycle_can_reach_live_execution !== false;
  const updatePolicy = <K extends keyof ExecutionRiskProfilePolicySettings>(
    key: K,
    value: ExecutionRiskProfilePolicySettings[K],
  ) => onPolicyChange({ ...form, [key]: value });

  return (
    <section className="rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">실행 리스크 프로파일</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            AI 추천과 deterministic 프로파일을 결합해 active ExecutionRiskProfile을 정합니다. 설정은 정책과
            검토 주기만 조정하며 leverage, slippage, position size 같은 주문 임계값은 AI가 직접 바꾸지 않습니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={form.advisor_enabled ? "good" : "warn"}>
            Advisor {form.advisor_enabled ? "enabled" : "disabled"}
          </StatusPill>
          <StatusPill tone={form.advisor_shadow_mode || form.auto_apply_mode === "shadow" ? "warn" : "neutral"}>
            apply mode: {form.auto_apply_mode}
          </StatusPill>
          <StatusPill tone={current.active_profile_blocks_new_entry ? "danger" : "neutral"}>
            신규 진입 {current.active_profile_blocks_new_entry ? "차단" : "프로파일 차단 없음"}
          </StatusPill>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">deterministic</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {formatDisplayValue(current.deterministic_profile ?? null)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">AI recommendation</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {formatDisplayValue(current.ai_recommended_profile ?? null)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            status={formatDisplayValue(current.ai_recommendation_status ?? null)} / confidence=
            {percentLabel(current.ai_recommendation_confidence)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">final active</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {formatDisplayValue(current.final_active_profile ?? null)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            shadow final={formatDisplayValue(current.shadow_final_profile ?? null)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">selector reason</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {formatDisplayValue(current.selected_reason ?? null)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            mode={formatDisplayValue(current.selection_mode ?? form.auto_apply_mode)}
          </p>
        </div>
      </div>

      <div className="mt-5 grid gap-4 rounded-md border border-slate-200 bg-white p-4 lg:grid-cols-2">
        <Toggle
          checked={form.advisor_enabled}
          label="AI 프로파일 Advisor 사용"
          onChange={(value) => updatePolicy("advisor_enabled", value)}
        />
        <Toggle
          checked={form.advisor_shadow_mode}
          label="Advisor shadow 기록 유지"
          onChange={(value) => updatePolicy("advisor_shadow_mode", value)}
        />
        <Field label="자동 적용 모드">
          <select
            className={inputClass}
            value={form.auto_apply_mode}
            onChange={(event) =>
              updatePolicy(
                "auto_apply_mode",
                event.target.value as ExecutionRiskProfilePolicySettings["auto_apply_mode"],
              )
            }
          >
            {autoApplyModeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="일반 검토 주기(분)">
          <input
            className={inputClass}
            type="number"
            min={1}
            value={secondsToMinutes(form.normal_interval_seconds)}
            onChange={(event) =>
              updatePolicy("normal_interval_seconds", minutesToSeconds(event.target.value, 1))
            }
          />
        </Field>
        <Field label="위험 감지 검토 주기(분)">
          <input
            className={inputClass}
            type="number"
            min={1}
            value={secondsToMinutes(form.elevated_interval_seconds)}
            onChange={(event) =>
              updatePolicy("elevated_interval_seconds", minutesToSeconds(event.target.value, 1))
            }
          />
        </Field>
        <Field label="최소 재검토 간격(분)">
          <input
            className={inputClass}
            type="number"
            min={1}
            value={secondsToMinutes(form.min_recheck_interval_seconds)}
            onChange={(event) =>
              updatePolicy("min_recheck_interval_seconds", minutesToSeconds(event.target.value, 1))
            }
          />
        </Field>
        <Field label="추천 TTL(분)">
          <input
            className={inputClass}
            type="number"
            min={1}
            value={secondsToMinutes(form.recommendation_ttl_seconds)}
            onChange={(event) =>
              updatePolicy("recommendation_ttl_seconds", minutesToSeconds(event.target.value, 1))
            }
          />
        </Field>
        <Field label="최소 신뢰도(%)">
          <input
            className={inputClass}
            type="number"
            min={50}
            max={100}
            value={Math.round(form.min_confidence_to_apply * 100)}
            onChange={(event) =>
              updatePolicy(
                "min_confidence_to_apply",
                Math.min(Math.max(Number(event.target.value) / 100, 0.5), 1),
              )
            }
          />
        </Field>
        <Field label="완화 확인 횟수">
          <input
            className={inputClass}
            type="number"
            min={1}
            max={10}
            value={form.relax_requires_consecutive_confirmations}
            onChange={(event) =>
              updatePolicy(
                "relax_requires_consecutive_confirmations",
                Math.max(Math.trunc(Number(event.target.value)), 1),
              )
            }
          />
        </Field>
        <Field label="프로파일 유지 시간(분)" hint="0분이면 완화 dwell 게이트를 사용하지 않습니다.">
          <input
            className={inputClass}
            type="number"
            min={0}
            value={secondsToMinutes(form.min_profile_dwell_seconds)}
            onChange={(event) =>
              updatePolicy("min_profile_dwell_seconds", minutesToSeconds(event.target.value, 0))
            }
          />
        </Field>
        <div className="flex flex-col gap-3 lg:col-span-2">
          <InlineFeedback message={feedback} />
          <button
            className="inline-flex min-h-11 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isPending}
            type="button"
            onClick={onSave}
          >
            실행 리스크 프로파일 설정 저장
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <StatusPill>normal review {secondsLabel(form.normal_interval_seconds)}</StatusPill>
        <StatusPill>elevated review {secondsLabel(form.elevated_interval_seconds)}</StatusPill>
        <StatusPill>min recheck {secondsLabel(form.min_recheck_interval_seconds)}</StatusPill>
        <StatusPill>TTL {secondsLabel(form.recommendation_ttl_seconds)}</StatusPill>
        <StatusPill>min confidence {percentLabel(form.min_confidence_to_apply)}</StatusPill>
      </div>

      <div className="mt-5 grid gap-3 xl:grid-cols-2">
        {profiles.map((profile) => (
          <div key={profile.profile_id} className="rounded-md border border-slate-200 bg-white px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-slate-900">{profile.profile_id}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {profile.label} / severity {profile.severity}
                </p>
              </div>
              <StatusPill tone={profileTone(profile)}>
                {profile.blocks_new_entry_when_active ? "차단 프로파일" : "관찰 조정"}
              </StatusPill>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-600">{profile.description}</p>
            <p className="mt-2 text-sm font-medium text-slate-800">{profile.new_entry_policy}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 rounded-md border border-dashed border-slate-300 bg-white px-4 py-4">
        <p className="text-sm font-semibold text-slate-900">동작 트리거</p>
        <div className="mt-3 grid gap-3 text-sm leading-6 text-slate-600 md:grid-cols-3">
          <p>Advisor는 decision cycle 안에서 실행되며 시장 상태와 runtime state를 보고 허용된 profile_id만 추천합니다.</p>
          <p>Selector는 risk_guard 평가 중 실행되어 deterministic profile과 AI 추천을 결합합니다.</p>
          <p>
            수동 트리거는{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5">
              {operation.manual_cycle_endpoint ?? "/api/cycles/run"}
            </code>
            입니다
            {canLiveTrigger
              ? ". 현재 live 설정에서는 주문 경로까지 이어질 수 있어 pause/disarm 상태를 함께 확인해야 합니다."
              : "."}
          </p>
        </div>
      </div>
    </section>
  );
}
