"use client";

import { Field, InlineFeedback, StatusPill, Toggle, inputClass, type FeedbackMessage } from "./form-primitives";
import { type EventSourceProvider } from "./types";

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

export function IntegrationSettingsPanel({
  form,
  state,
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
  return (
    <div className="space-y-5">
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            여기서는 제공자, 모델, 입력 길이, 온도만 조정합니다. 호출 타이밍은 위 운영 주기 섹션에서 관리하고,
            신규 진입은 이벤트 기반 + 행동 바운딩 + 실패 시 차단 경로를 따릅니다.
          </p>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
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
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">외부 이벤트 소스</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                FRED 기반 매크로 일정 source를 settings에서 고정하거나, 비워 두고 기존 env fallback을 유지할 수 있습니다.
                아래 BLS/BEA enrichment API는 발표가 지난 이벤트의 actual 값을 보강하는 observe-only layer이며
                `risk_guard`를 직접 바꾸지 않습니다.
              </p>
            </div>
            <StatusPill tone={state.event_source_provider === "fred" ? "good" : "neutral"}>
              {eventSourceProviderLabel}
            </StatusPill>
          </div>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 런타임 event source</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{eventSourceProvenanceLabel}</p>
            {eventSourceVendorLabel ? (
              <p className="mt-2 text-sm text-slate-700">primary calendar vendor: {eventSourceVendorLabel}</p>
            ) : null}
            <p className="mt-1 text-sm text-slate-700">post-release enrichment: {eventEnrichmentLabel}</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{eventSourceHelp}</p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle
              checked={eventSourceOverrideEnabled}
              label="settings 값 우선 사용"
              onChange={(value) => onFieldChange("event_source_provider", value ? (form.event_source_provider || "stub") : "")}
            />
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
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
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-4">
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

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
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

      <div className="flex flex-col gap-3 rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
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
