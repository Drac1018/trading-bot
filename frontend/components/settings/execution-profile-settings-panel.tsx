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
    label: "정상",
    description: "기본 시장 상태입니다.",
    new_entry_policy: "일반 신규 진입 검토 가능",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "CAUTION",
    severity: 1,
    label: "주의",
    description: "불확실성이 올라간 상태입니다.",
    new_entry_policy: "최종 리스크 검증 후 진입 가능",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "HIGH_VOLATILITY",
    severity: 2,
    label: "변동성 확대",
    description: "변동성 확대 또는 가격 범위 이탈 상태입니다.",
    new_entry_policy: "더 보수적인 확인 상태",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "THIN_LIQUIDITY",
    severity: 2,
    label: "유동성 얇음",
    description: "스프레드/유동성 상태가 불리합니다.",
    new_entry_policy: "더 보수적인 확인 상태",
    blocks_new_entry_when_active: false,
  },
  {
    profile_id: "STRESS",
    severity: 3,
    label: "위험 확대",
    description: "시장/운영 리스크가 높은 상태입니다.",
    new_entry_policy: "적용 모드에서 신규 진입 차단",
    blocks_new_entry_when_active: true,
  },
  {
    profile_id: "DEGRADED",
    severity: 4,
    label: "보호 확인 필요",
    description: "데이터, 동기화, 보호 주문 확인이 필요한 상태입니다.",
    new_entry_policy: "신규 진입 차단, 축소/청산 경로는 분리",
    blocks_new_entry_when_active: true,
  },
];

const autoApplyModeOptions: Array<{
  value: ExecutionRiskProfilePolicySettings["auto_apply_mode"];
  label: string;
}> = [
  { value: "off", label: "자동 적용 안 함" },
  { value: "shadow", label: "관찰만" },
  { value: "conservative_only", label: "보수화만 적용" },
  { value: "manual_approval", label: "수동 승인" },
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

const profileDisplayLabels: Record<string, string> = {
  NORMAL: "정상",
  CAUTION: "주의",
  HIGH_VOLATILITY: "변동성 확대",
  THIN_LIQUIDITY: "유동성 얇음",
  STRESS: "위험 확대",
  DEGRADED: "보호 확인 필요",
};

const recommendationStatusLabels: Record<string, string> = {
  ignored: "무시됨",
  shadow_generated: "관찰 결과 생성",
  valid: "유효",
  expired: "만료",
};

const selectedReasonLabels: Record<string, string> = {
  ai_tightened_conservative_only: "AI 보수화 적용",
  ai_relaxation_blocked: "AI 완화 차단",
  deterministic_fallback: "기본 규칙 유지",
  no_ai_recommendation: "AI 추천 없음",
  profile_relaxation_blocked: "프로파일 완화 차단",
  shadow_no_auto_apply: "관찰 모드",
};

function profileDisplayLabel(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return profileDisplayLabels[value.toUpperCase()] ?? value;
}

function profileDescriptionLabel(profile: ExecutionRiskProfileDefinition) {
  const descriptions: Record<string, string> = {
    NORMAL: "기본 시장 상태입니다. 추가 프로파일 차단 없이 일반 리스크 점검 결과를 따릅니다.",
    CAUTION: "불확실성이 올라간 상태입니다. AI 추천은 더 보수적인 검토 신호로만 사용합니다.",
    HIGH_VOLATILITY: "변동성 확대 또는 가격 범위 이탈이 감지된 상태입니다. 추격 진입보다 확인 절차를 우선합니다.",
    THIN_LIQUIDITY: "스프레드와 유동성 상태가 불리합니다. 비용과 체결 품질 리스크를 더 크게 봅니다.",
    STRESS: "시장 또는 운영 리스크가 높은 상태입니다. 적용 모드에서는 신규 진입을 막습니다.",
    DEGRADED: "데이터, 동기화, 보호 주문 확인이 필요한 상태입니다. 신규 진입은 막고 축소/청산 경로는 분리합니다.",
  };
  return descriptions[profile.profile_id.toUpperCase()] ?? profile.description;
}

function profileNewEntryPolicyLabel(profile: ExecutionRiskProfileDefinition) {
  const policies: Record<string, string> = {
    NORMAL: "일반 신규 진입 검토 가능",
    CAUTION: "최종 리스크 검증 후 진입 가능",
    HIGH_VOLATILITY: "더 보수적인 확인 상태로 분류",
    THIN_LIQUIDITY: "더 보수적인 확인 상태로 분류",
    STRESS: "적용 시 신규 진입 차단",
    DEGRADED: "신규 진입 차단, 축소/청산 경로는 분리",
  };
  return policies[profile.profile_id.toUpperCase()] ?? profile.new_entry_policy;
}

function autoApplyModeLabel(value: string | null | undefined) {
  const option = autoApplyModeOptions.find((item) => item.value === value);
  return option?.label ?? formatDisplayValue(value ?? null);
}

function recommendationStatusLabel(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return recommendationStatusLabels[value.toLowerCase()] ?? value;
}

function selectedReasonLabel(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return selectedReasonLabels[value] ?? value;
}

function profileBlockStatusLabel(blocked: boolean | null | undefined) {
  return blocked ? "차단 적용" : "차단 미적용";
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
  const currentFinalProfile = current.final_active_profile ?? null;
  const currentProfileBlockStatus = profileBlockStatusLabel(current.active_profile_blocks_new_entry);
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
            AI 추천과 규칙 기반 시장 상태를 결합해 실행 프로파일을 정합니다. 여기서는 적용 방식과 검토 주기만 조정하며
            주문 수량, 슬리피지 한도, 레버리지 임계값은 별도 리스크 설정을 따릅니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={form.advisor_enabled ? "good" : "warn"}>
            AI 검토 {form.advisor_enabled ? "사용" : "중지"}
          </StatusPill>
          <StatusPill tone={form.advisor_shadow_mode || form.auto_apply_mode === "shadow" ? "warn" : "neutral"}>
            적용 방식: {autoApplyModeLabel(form.auto_apply_mode)}
          </StatusPill>
          <StatusPill tone={current.active_profile_blocks_new_entry ? "danger" : "neutral"}>
            현재 신규 진입 {currentProfileBlockStatus}
          </StatusPill>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">기본 규칙</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {profileDisplayLabel(current.deterministic_profile)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">AI 추천</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {profileDisplayLabel(current.ai_recommended_profile)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            상태 {recommendationStatusLabel(current.ai_recommendation_status)} / 신뢰도 {percentLabel(current.ai_recommendation_confidence)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">최종 적용</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {profileDisplayLabel(currentFinalProfile)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {currentProfileBlockStatus} / 관찰 예상값 {profileDisplayLabel(current.shadow_final_profile)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <p className="text-xs text-slate-500">선택 사유</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {selectedReasonLabel(current.selected_reason)}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            적용 방식 {autoApplyModeLabel(current.selection_mode ?? form.auto_apply_mode)}
          </p>
        </div>
      </div>

      <div className="mt-5 grid gap-4 rounded-md border border-slate-200 bg-white p-4 lg:grid-cols-2">
        <Toggle
          checked={form.advisor_enabled}
          label="AI 프로파일 검토 사용"
          onChange={(value) => updatePolicy("advisor_enabled", value)}
        />
        <Toggle
          checked={form.advisor_shadow_mode}
          label="AI 추천을 관찰 기록으로 유지"
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
        <Field label="추천 유효 시간(분)">
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
        <Field label="프로파일 유지 시간(분)" hint="0분이면 완화 유지 시간 조건을 사용하지 않습니다.">
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
        <StatusPill>일반 검토 {secondsLabel(form.normal_interval_seconds)}</StatusPill>
        <StatusPill>위험 감지 검토 {secondsLabel(form.elevated_interval_seconds)}</StatusPill>
        <StatusPill>최소 재검토 {secondsLabel(form.min_recheck_interval_seconds)}</StatusPill>
        <StatusPill>추천 유효 시간 {secondsLabel(form.recommendation_ttl_seconds)}</StatusPill>
        <StatusPill>최소 신뢰도 {percentLabel(form.min_confidence_to_apply)}</StatusPill>
      </div>

      <div className="mt-5 grid gap-3 xl:grid-cols-2">
        {profiles.map((profile) => {
          const isCurrentProfile = currentFinalProfile === profile.profile_id;
          return (
            <div
              key={profile.profile_id}
              className={`rounded-md border px-4 py-4 ${
                isCurrentProfile ? "border-blue-200 bg-blue-50" : "border-slate-200 bg-white"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{profileDisplayLabel(profile.profile_id)}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    위험 단계 {profile.severity}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {isCurrentProfile ? (
                    <StatusPill tone={current.active_profile_blocks_new_entry ? "danger" : "neutral"}>
                      현재 적용
                    </StatusPill>
                  ) : null}
                  <StatusPill tone={profileTone(profile)}>
                    {profile.blocks_new_entry_when_active ? "적용 시 차단 프로파일" : "관찰 조정"}
                  </StatusPill>
                </div>
              </div>
              <p className="mt-3 text-sm leading-6 text-slate-600">{profileDescriptionLabel(profile)}</p>
              <p className="mt-2 text-sm font-medium text-slate-800">{profileNewEntryPolicyLabel(profile)}</p>
              {isCurrentProfile ? (
                <p className="mt-2 text-xs leading-5 text-slate-600">
                  현재 선택 결과: {currentProfileBlockStatus}. 실제 신규 진입 차단 여부는 프로파일 이름만 보지 말고
                  운영 상태, 동기화, 실거래 승인 사유를 함께 확인하세요.
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="mt-5 rounded-md border border-dashed border-slate-300 bg-white px-4 py-4">
        <p className="text-sm font-semibold text-slate-900">동작 트리거</p>
        <div className="mt-3 grid gap-3 text-sm leading-6 text-slate-600 md:grid-cols-3">
          <p>AI 검토는 판단 주기 안에서 실행되며 시장 상태와 운영 상태를 보고 허용된 프로파일만 추천합니다.</p>
          <p>프로파일 선택기는 리스크 평가 중 실행되어 기본 규칙과 AI 추천을 결합합니다.</p>
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
