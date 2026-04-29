"use client";

import { Field, InlineFeedback, StatusPill, Toggle, inputClass, type FeedbackMessage } from "./form-primitives";
import { type AIModelRoutePolicyItem, type AIModelRoutingPolicy, type EventSourceProvider } from "./types";

type IntegrationForm = {
  ai_enabled: boolean;
  ai_provider: "openai" | "mock";
  ai_model: string;
  ai_temperature: number;
  ai_max_input_candles: number;
  openai_api_key: string;
  clear_openai_api_key: boolean;
  event_source_provider: "" | EventSourceProvider;
  event_source_api_key: string;
  event_source_api_url: string;
  event_source_timeout_seconds: number | null;
  event_source_default_assets_input: string;
  event_source_fred_release_ids_input: string;
  event_source_bls_enrichment_url: string;
  event_source_bea_enrichment_url: string;
  clear_event_source_api_key: boolean;
  binance_market_data_enabled: boolean;
  binance_futures_enabled: boolean;
  binance_testnet_enabled: boolean;
  binance_api_key: string;
  binance_api_secret: string;
  clear_binance_api_key: boolean;
  clear_binance_api_secret: boolean;
};

type IntegrationState = {
  event_source_provider: EventSourceProvider | null;
  event_source_api_key_configured: boolean;
};

function numberOrNull(value: string) {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

const eventSourceProviderOptions: EventSourceProvider[] = ["stub", "fred"];
const aiModelPresets = ["gpt-4.1-mini", "gpt-5-mini"] as const;

function fallbackAiModelRoutingPolicy(primaryModel: string): AIModelRoutingPolicy {
  return {
    primary_model: primaryModel,
    read_only: true,
    runtime_model_source: "settings.ai_model",
    summary:
      "기존 payload 기준입니다. AI 호출 전 생략과 대시보드 읽기 모델은 모델을 호출하지 않고, 제공자 호출 경로는 설정된 모델을 사용합니다.",
    no_model_call_routes: ["pre_ai_skip_simple_classification", "daily_dashboard_explanation"],
    candidate_model_routes: ["macro_event_position_complex", "operator_manual_high_risk"],
    routes: [
      {
        route: "pre_ai_skip_simple_classification",
        label: "AI 호출 전 생략 / 단순 분류",
        call_policy: "no_model_call",
        configured_model: null,
        model_candidates: [],
      },
      {
        route: "general_entry_review",
        label: "일반 진입 검토",
        call_policy: "provider_invoked_when_gate_allows",
        configured_model: primaryModel,
        default_model: "gpt-4.1-mini",
        model_candidates: ["gpt-4.1-mini"],
      },
      {
        route: "macro_event_position_complex",
        label: "거시 이벤트 + 포지션 + 복합 판단",
        call_policy: "candidate_model_tier",
        configured_model: primaryModel,
        default_model: "gpt-4.1-mini",
        model_candidates: ["gpt-4.1-mini", "gpt-5-mini"],
      },
      {
        route: "operator_manual_high_risk",
        label: "운영자 수동 검토 / 고위험 상황",
        call_policy: "candidate_model_tier",
        configured_model: primaryModel,
        default_model: "gpt-5-mini",
        model_candidates: ["gpt-5-mini", "상위 모델 직접 입력"],
      },
      {
        route: "daily_dashboard_explanation",
        label: "일상 대시보드 설명",
        call_policy: "read_model_no_model_call",
        configured_model: null,
        model_candidates: [],
      },
    ],
  };
}

function routeLabel(route: AIModelRoutePolicyItem) {
  const labels: Record<string, string> = {
    pre_ai_skip_simple_classification: "AI 호출 전 생략 / 단순 분류",
    general_entry_review: "일반 진입 검토",
    macro_event_position_complex: "거시 이벤트 + 포지션 + 복합 판단",
    operator_manual_high_risk: "운영자 수동 검토 / 고위험 상황",
    daily_dashboard_explanation: "일상 대시보드 설명",
  };
  return labels[route.route] ?? route.label ?? route.route;
}

function callPolicyLabel(route: AIModelRoutePolicyItem) {
  if (route.call_policy === "no_model_call" || route.call_policy === "read_model_no_model_call") {
    return "모델 호출 없음";
  }
  if (route.call_policy === "candidate_model_tier") {
    return "후보 모델";
  }
  return "설정 모델 사용";
}

function callPolicyTone(route: AIModelRoutePolicyItem): "neutral" | "good" | "warn" {
  if (route.call_policy === "no_model_call" || route.call_policy === "read_model_no_model_call") {
    return "good";
  }
  if (route.call_policy === "candidate_model_tier") {
    return "warn";
  }
  return "neutral";
}

function modelCandidateLabel(model: string) {
  return model === "higher_model_option" ? "상위 모델 직접 입력" : model;
}

export function IntegrationSettingsPanel({
  form,
  state,
  aiModelRoutingPolicy,
  eventSourceProvenanceLabel,
  eventSourceVendorLabel,
  eventEnrichmentLabel,
  eventSourceHelp,
  eventSourceOverrideEnabled,
  eventSourceProviderLabel,
  blsEnrichmentConfigState,
  beaEnrichmentConfigState,
  isPending,
  feedback,
  onFieldChange,
  onSave,
}: {
  form: IntegrationForm;
  state: IntegrationState;
  aiModelRoutingPolicy?: AIModelRoutingPolicy | null;
  eventSourceProvenanceLabel: string;
  eventSourceVendorLabel: string | null;
  eventEnrichmentLabel: string;
  eventSourceHelp: string;
  eventSourceOverrideEnabled: boolean;
  eventSourceProviderLabel: string;
  blsEnrichmentConfigState: string;
  beaEnrichmentConfigState: string;
  isPending: boolean;
  feedback?: FeedbackMessage;
  onFieldChange: (field: keyof IntegrationForm, value: IntegrationForm[keyof IntegrationForm]) => void;
  onSave: () => void;
}) {
  const modelRoutingPolicy =
    aiModelRoutingPolicy && Array.isArray(aiModelRoutingPolicy.routes) && aiModelRoutingPolicy.routes.length > 0
      ? aiModelRoutingPolicy
      : fallbackAiModelRoutingPolicy(form.ai_model);
  const modelRoutes = modelRoutingPolicy.routes ?? [];

  return (
    <div className="space-y-5">
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            여기서는 제공자, 모델, 입력 길이, 온도만 조정합니다. 호출 타이밍은 위 운영 주기 섹션에서 관리하고,
            신규 진입은 이벤트 기반 + 행동 바운딩 + 실패 시 차단 경로를 따릅니다.
          </p>
          <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 AI 운영 원칙</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              고정 15분 AI 호출이 아니라 트리거 기반으로만 평가를 시도합니다. 위의 재검토 확인 주기는 주기 cycle이
              재검토 이벤트를 찾는 간격이고, AI 기본 검토 간격은 열린 포지션 재검토 기준과 수동 재실행 보호에
              사용됩니다.
            </p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.ai_enabled} label="OpenAI 사용" onChange={(value) => onFieldChange("ai_enabled", value)} />
            <Field label="제공자">
              <select
                className={inputClass}
                value={form.ai_provider}
                onChange={(event) => onFieldChange("ai_provider", event.target.value as IntegrationForm["ai_provider"])}
              >
                <option value="openai">OpenAI</option>
                <option value="mock">모의 응답</option>
              </select>
            </Field>
            <Field label="모델">
              <input className={inputClass} value={form.ai_model} onChange={(event) => onFieldChange("ai_model", event.target.value)} />
              <div className="mt-2 flex flex-wrap gap-2">
                {aiModelPresets.map((model) => (
                  <button
                    key={model}
                    type="button"
                    className={`rounded-md border px-3 py-1 text-xs font-semibold transition ${
                      form.ai_model === model
                        ? "border-blue-300 bg-blue-50 text-blue-700"
                        : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                    }`}
                    onClick={() => onFieldChange("ai_model", model)}
                  >
                    {model}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="온도" hint="낮게 유지할수록 응답 분산이 줄어듭니다.">
              <input
                className={inputClass}
                type="number"
                min={0}
                max={1}
                step="0.05"
                value={form.ai_temperature}
                onChange={(event) => onFieldChange("ai_temperature", Number(event.target.value))}
              />
            </Field>
            <Field label="AI 입력 캔들 수">
              <input
                className={inputClass}
                type="number"
                min={16}
                max={200}
                value={form.ai_max_input_candles}
                onChange={(event) => onFieldChange("ai_max_input_candles", Number(event.target.value))}
              />
            </Field>
            <Field label="OpenAI API 키">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={form.openai_api_key}
                onChange={(event) => onFieldChange("openai_api_key", event.target.value)}
                placeholder="sk-..."
              />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={form.clear_openai_api_key}
                onChange={(event) => onFieldChange("clear_openai_api_key", event.target.checked)}
              />
              저장된 키 제거
            </label>
          </div>
          <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900">AI 모델 호출 정책</p>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  {modelRoutingPolicy.summary ??
                    "AI 호출 전 생략과 대시보드 읽기 모델은 모델을 호출하지 않고, 제공자 호출 경로는 설정된 모델을 사용합니다."}
                </p>
              </div>
              <StatusPill tone={modelRoutingPolicy.read_only === false ? "warn" : "neutral"}>
                {modelRoutingPolicy.read_only === false ? "런타임 라우팅" : "읽기 전용"}
              </StatusPill>
            </div>
            <div className="mt-4 grid gap-3">
              {modelRoutes.map((route) => {
                const candidates = route.model_candidates?.filter(Boolean).map(modelCandidateLabel) ?? [];
                const modelText =
                  route.call_policy === "no_model_call" || route.call_policy === "read_model_no_model_call"
                    ? "모델 호출 없음"
                    : route.call_policy === "candidate_model_tier" && candidates.length > 0
                      ? candidates.join(" / ")
                      : route.configured_model ?? route.default_model ?? "모델 없음";
                return (
                  <div key={route.route} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-slate-900">{routeLabel(route)}</p>
                      <StatusPill tone={callPolicyTone(route)}>{callPolicyLabel(route)}</StatusPill>
                    </div>
                    <p className="mt-2 text-sm text-slate-700">{modelText}</p>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">외부 이벤트 소스</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                FRED 기반 매크로 일정 소스를 설정에서 고정하거나, 비워 두고 기존 환경 변수 대체 경로를 유지할 수 있습니다.
                아래 BLS/BEA 보강 API는 발표가 지난 이벤트의 실제값을 보강하는 관찰 전용 계층이며
                리스크 가드를 직접 바꾸지 않습니다.
              </p>
            </div>
            <StatusPill tone={state.event_source_provider === "fred" ? "good" : "neutral"}>
              {eventSourceProviderLabel}
            </StatusPill>
          </div>
          <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 런타임 이벤트 소스</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{eventSourceProvenanceLabel}</p>
            {eventSourceVendorLabel ? (
              <p className="mt-2 text-sm text-slate-700">주 일정 제공자: {eventSourceVendorLabel}</p>
            ) : null}
            <p className="mt-1 text-sm text-slate-700">발표 후 실제값 보강: {eventEnrichmentLabel}</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{eventSourceHelp}</p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle
              checked={eventSourceOverrideEnabled}
              label="settings 값 우선 사용"
              onChange={(value) => onFieldChange("event_source_provider", value ? (form.event_source_provider || "stub") : "")}
            />
            <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">적용 방식</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {eventSourceOverrideEnabled ? "저장된 settings 값 우선" : "env fallback 또는 stub"}
              </p>
            </div>
            <Field
              label="소스 제공자"
              hint={
                eventSourceOverrideEnabled
                  ? "1차는 stub / fred만 노출합니다."
                  : "settings override를 켜면 stub 또는 fred를 저장할 수 있습니다."
              }
            >
              <select
                className={inputClass}
                disabled={!eventSourceOverrideEnabled}
                value={form.event_source_provider || "stub"}
                onChange={(event) =>
                  onFieldChange("event_source_provider", event.target.value as IntegrationForm["event_source_provider"])
                }
              >
                {eventSourceProviderOptions.map((option) => (
                  <option key={option} value={option}>
                    {option === "fred" ? "FRED" : "stub (미연결/기본)"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="FRED API 키" hint="비워 두면 기존 저장값을 유지합니다. settings override를 끄면 런타임에서는 env fallback만 사용합니다.">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={form.event_source_api_key}
                onChange={(event) => onFieldChange("event_source_api_key", event.target.value)}
                placeholder="FRED API key"
              />
            </Field>
            <Field label="API URL" hint="비우면 settings override에서는 FRED 기본 URL, override 미사용이면 env 값을 따릅니다.">
              <input
                className={inputClass}
                value={form.event_source_api_url}
                onChange={(event) => onFieldChange("event_source_api_url", event.target.value)}
                placeholder="https://api.stlouisfed.org/fred"
              />
            </Field>
            <Field label="Timeout (seconds)" hint="비우면 env fallback 또는 기본 10초를 사용합니다.">
              <input
                className={inputClass}
                type="number"
                min={1}
                max={120}
                step="1"
                value={form.event_source_timeout_seconds ?? ""}
                onChange={(event) => onFieldChange("event_source_timeout_seconds", numberOrNull(event.target.value))}
              />
            </Field>
            <Field label="기본 자산" hint="예: BTCUSDT, ETHUSDT. 비우면 env fallback 또는 현재 심볼을 사용합니다.">
              <input
                className={inputClass}
                value={form.event_source_default_assets_input}
                onChange={(event) => onFieldChange("event_source_default_assets_input", event.target.value.toUpperCase())}
                placeholder="BTCUSDT, ETHUSDT"
              />
            </Field>
            <Field label="FRED release IDs" hint="예: 10, 46, 50, 53, 101">
              <input
                className={inputClass}
                value={form.event_source_fred_release_ids_input}
                onChange={(event) => onFieldChange("event_source_fred_release_ids_input", event.target.value)}
                placeholder="10, 46, 50, 53, 101"
              />
            </Field>
          </div>
          <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900">발표 후 actual enrichment API</p>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  FRED가 다음 일정과 리스크 윈도우를 유지하고, BLS/BEA는 발표가 지난 이벤트의 actual/prior 값을
                  보강합니다. 비워 두면 settings 값 대신 env fallback을 사용합니다.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusPill tone={form.event_source_bls_enrichment_url ? "good" : "neutral"}>
                  BLS: {blsEnrichmentConfigState}
                </StatusPill>
                <StatusPill tone={form.event_source_bea_enrichment_url ? "good" : "neutral"}>
                  BEA: {beaEnrichmentConfigState}
                </StatusPill>
              </div>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <Field
                label="BLS enrichment URL"
                hint="예: CPI/PPI/고용 actual 값을 normalize contract로 돌려주는 wrapper endpoint. series 매핑은 wrapper 내부에서 관리하며, 이 화면에서는 URL만 넣습니다."
              >
                <input
                  className={inputClass}
                  value={form.event_source_bls_enrichment_url}
                  onChange={(event) => onFieldChange("event_source_bls_enrichment_url", event.target.value)}
                  placeholder="https://example.local/bls/releases"
                />
              </Field>
              <Field
                label="BEA enrichment URL"
                hint="예: GDP/PCE actual 값을 normalize contract로 돌려주는 wrapper endpoint. dataset/table 매핑은 wrapper 내부에서 관리하며, 이 화면에서는 URL만 넣습니다."
              >
                <input
                  className={inputClass}
                  value={form.event_source_bea_enrichment_url}
                  onChange={(event) => onFieldChange("event_source_bea_enrichment_url", event.target.value)}
                  placeholder="https://example.local/bea/releases"
                />
              </Field>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={form.clear_event_source_api_key}
                onChange={(event) => onFieldChange("clear_event_source_api_key", event.target.checked)}
              />
              저장된 FRED 키 제거
            </label>
            <StatusPill tone={state.event_source_api_key_configured ? "good" : "neutral"}>
              {state.event_source_api_key_configured ? "저장된 FRED 키 있음" : "저장된 FRED 키 없음"}
            </StatusPill>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            실사용에서는 백엔드 스케줄러가 FRED 일정을 읽고 발표 시각이 지난 이벤트에만 BLS/BEA enrichment URL을 자동
            호출합니다. 별도 테스트 입력이나 series_id 수동 입력은 사용하지 않으며, 기존 static params가 있어도 이
            화면에서는 그대로 유지됩니다.
          </p>
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">Binance 연동</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            시세 사용 여부, 선물 / 테스트넷 경로, API 자격증명을 관리합니다. 실제 계좌 상태 확인은 위 실거래 제어의
            거래소 동기화 버튼을 사용합니다.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle
              checked={form.binance_market_data_enabled}
              label="Binance 시세 사용"
              onChange={(value) => onFieldChange("binance_market_data_enabled", value)}
            />
            <Toggle
              checked={form.binance_futures_enabled}
              label="USD-M 선물"
              onChange={(value) => onFieldChange("binance_futures_enabled", value)}
            />
            <Toggle
              checked={form.binance_testnet_enabled}
              label="테스트넷 사용"
              onChange={(value) => onFieldChange("binance_testnet_enabled", value)}
            />
            <Field label="Binance API 키">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={form.binance_api_key}
                onChange={(event) => onFieldChange("binance_api_key", event.target.value)}
              />
            </Field>
            <Field label="Binance API 시크릿">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={form.binance_api_secret}
                onChange={(event) => onFieldChange("binance_api_secret", event.target.value)}
              />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={form.clear_binance_api_key}
                onChange={(event) => onFieldChange("clear_binance_api_key", event.target.checked)}
              />
              저장된 키 제거
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={form.clear_binance_api_secret}
                onChange={(event) => onFieldChange("clear_binance_api_secret", event.target.checked)}
              />
              저장된 시크릿 제거
            </label>
          </div>
        </div>
      </section>

      <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
        <p className="text-sm text-slate-600">
          연동 설정도 기존 full payload 저장 경로를 그대로 사용합니다. OpenAI/Binance/FRED 키 변경은 저장 후
          반영됩니다.
        </p>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <button
            className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60"
            disabled={isPending}
            onClick={onSave}
            type="button"
          >
            {isPending ? "저장 중..." : "연동 설정 저장"}
          </button>
          <div className="lg:min-w-[18rem]">
            <InlineFeedback message={feedback} />
          </div>
        </div>
      </div>
    </div>
  );
}

