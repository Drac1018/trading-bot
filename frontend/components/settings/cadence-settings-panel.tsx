"use client";

import { AIUsagePanel, type AIUsagePayload } from "../ai-usage-panel";
import { Field, InlineFeedback, StatusPill, Toggle, inputClass, type FeedbackMessage } from "./form-primitives";
import { formatDisplayValue } from "../../lib/ui-copy";
import { type SymbolCadenceOverride, type SymbolEffectiveCadence } from "./types";

type CadenceForm = {
  default_symbol: string;
  tracked_symbols: string[];
  custom_symbols: string;
  default_timeframe: string;
  max_leverage: number;
  max_risk_per_trade: number;
  max_daily_loss: number;
  max_consecutive_losses: number;
  stale_market_seconds: number;
  slippage_threshold_pct: number;
  exchange_sync_interval_seconds: number;
  market_refresh_interval_minutes: number;
  position_management_interval_seconds: number;
  decision_cycle_interval_minutes: number;
  ai_call_interval_minutes: number;
  adaptive_signal_enabled: boolean;
  position_management_enabled: boolean;
  break_even_enabled: boolean;
  atr_trailing_stop_enabled: boolean;
  partial_take_profit_enabled: boolean;
  holding_edge_decay_enabled: boolean;
  reduce_on_regime_shift_enabled: boolean;
};

function numberOrNull(value: string) {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function hasCadenceCustomization(row: SymbolCadenceOverride) {
  return (
    !row.enabled ||
    Boolean(row.timeframe_override?.trim()) ||
    row.market_refresh_interval_minutes_override !== null ||
    row.position_management_interval_seconds_override !== null ||
    row.decision_cycle_interval_minutes_override !== null ||
    row.ai_call_interval_minutes_override !== null
  );
}

function SymbolCadenceOverridePanel({
  mergedSymbols,
  overrideRows,
  effectiveCadenceBySymbol,
  form,
  onSymbolOverrideChange,
}: {
  mergedSymbols: string[];
  overrideRows: SymbolCadenceOverride[];
  effectiveCadenceBySymbol: Record<string, SymbolEffectiveCadence>;
  form: CadenceForm;
  onSymbolOverrideChange: (symbol: string, patch: Partial<SymbolCadenceOverride>) => void;
}) {
  const customizedRows = overrideRows.filter((row) => hasCadenceCustomization(row));
  const customizedSymbolsSummary =
    customizedRows.length > 0 ? customizedRows.map((row) => row.symbol).join(", ") : "예외 심볼 없음";

  return (
    <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold text-slate-900">심볼별 운영 주기 override</h3>
          <StatusPill>{mergedSymbols.length}개 심볼</StatusPill>
          <StatusPill tone={customizedRows.length > 0 ? "warn" : "neutral"}>
            예외 적용 {customizedRows.length}개
          </StatusPill>
        </div>
        <p className="text-sm leading-6 text-slate-600">
          기본 화면에서는 전역 운영 주기를 먼저 보고, 예외 심볼은 필요할 때만 열어 수정합니다. 비워 두면 전역 기본값을 그대로 상속합니다.
        </p>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-4">
          <p className="text-xs text-slate-500">전역 운영 주기 우선</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            시장 {form.market_refresh_interval_minutes}분 / 포지션 {form.position_management_interval_seconds}초 / 재검토{" "}
            {form.decision_cycle_interval_minutes}분 / AI 기준 {form.ai_call_interval_minutes}분
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            기본 화면에서는 전역 기본값만 빠르게 확인하고, 예외 심볼은 아래 고급 설정에서만 수정합니다.
          </p>
        </div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-4">
          <p className="text-xs text-slate-500">예외 심볼 요약</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {customizedRows.length}개 심볼에 override 또는 운영 제외 적용
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-600">{customizedSymbolsSummary}</p>
        </div>
      </div>
      <details className="mt-4 rounded-2xl border border-dashed border-amber-300 bg-white">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">고급 설정: 심볼별 override 상세</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              기본 화면 복잡도를 줄이기 위해 상세 입력은 접어 두고, 필요할 때만 펼쳐 수정합니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusPill tone={customizedRows.length > 0 ? "warn" : "neutral"}>
              예외 {customizedRows.length}개
            </StatusPill>
            <StatusPill tone="neutral">펼쳐서 수정</StatusPill>
          </div>
        </summary>
        <div className="border-t border-amber-100 px-4 py-4">
          <div className="grid gap-4 2xl:grid-cols-2">
            {overrideRows.map((row) => {
              const effective = effectiveCadenceBySymbol[row.symbol];
              return (
                <div key={row.symbol} className="rounded-2xl border border-amber-200 bg-canvas p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-base font-semibold text-slate-900">{row.symbol}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <StatusPill tone={row.enabled ? "good" : "warn"}>
                          {row.enabled ? "운영 사용" : "운영 제외"}
                        </StatusPill>
                        <StatusPill tone={effective?.uses_global_defaults ? "neutral" : "warn"}>
                          {effective?.uses_global_defaults ? "전역 상속" : "override 적용"}
                        </StatusPill>
                      </div>
                    </div>
                    <label className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-white px-3 py-2 text-sm font-medium text-slate-700">
                      <input
                        checked={row.enabled}
                        onChange={(event) => onSymbolOverrideChange(row.symbol, { enabled: event.target.checked })}
                        type="checkbox"
                      />
                      사용
                    </label>
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    <Field label="타임프레임 override" hint="비우면 전역 타임프레임을 사용합니다.">
                      <input
                        className={inputClass}
                        value={row.timeframe_override ?? ""}
                        onChange={(event) =>
                          onSymbolOverrideChange(row.symbol, { timeframe_override: event.target.value || null })
                        }
                        placeholder={form.default_timeframe}
                      />
                    </Field>
                    <Field label="시장 갱신(분)" hint={`전역 ${form.market_refresh_interval_minutes}분`}>
                      <input
                        className={inputClass}
                        type="number"
                        min={1}
                        max={1440}
                        value={row.market_refresh_interval_minutes_override ?? ""}
                        onChange={(event) =>
                          onSymbolOverrideChange(row.symbol, {
                            market_refresh_interval_minutes_override: numberOrNull(event.target.value),
                          })
                        }
                        placeholder={`${form.market_refresh_interval_minutes}`}
                      />
                    </Field>
                    <Field label="포지션 관리(초)" hint={`전역 ${form.position_management_interval_seconds}초`}>
                      <input
                        className={inputClass}
                        type="number"
                        min={30}
                        max={3600}
                        value={row.position_management_interval_seconds_override ?? ""}
                        onChange={(event) =>
                          onSymbolOverrideChange(row.symbol, {
                            position_management_interval_seconds_override: numberOrNull(event.target.value),
                          })
                        }
                        placeholder={`${form.position_management_interval_seconds}`}
                      />
                    </Field>
                    <Field
                      label="재검토 확인 주기(분)"
                      hint={`전역 ${form.decision_cycle_interval_minutes}분 · 주기 cycle이 재검토 이벤트를 확인하는 기준`}
                    >
                      <input
                        className={inputClass}
                        type="number"
                        min={1}
                        max={1440}
                        value={row.decision_cycle_interval_minutes_override ?? ""}
                        onChange={(event) =>
                          onSymbolOverrideChange(row.symbol, {
                            decision_cycle_interval_minutes_override: numberOrNull(event.target.value),
                          })
                        }
                        placeholder={`${form.decision_cycle_interval_minutes}`}
                      />
                    </Field>
                    <Field
                      label="AI 기본 검토 간격(분)"
                      hint={`전역 ${form.ai_call_interval_minutes}분 · 열린 포지션 재검토 기준과 수동 재실행 보호에 사용`}
                    >
                      <input
                        className={inputClass}
                        type="number"
                        min={5}
                        max={1440}
                        value={row.ai_call_interval_minutes_override ?? ""}
                        onChange={(event) =>
                          onSymbolOverrideChange(row.symbol, {
                            ai_call_interval_minutes_override: numberOrNull(event.target.value),
                          })
                        }
                        placeholder={`${form.ai_call_interval_minutes}`}
                      />
                    </Field>
                  </div>

                  <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4">
                    {effective ? (
                      <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                          <div>
                            <p className="text-xs text-slate-500">타임프레임</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">{effective.timeframe}</p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-500">시장 갱신</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {effective.market_refresh_interval_minutes}분
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-500">포지션 관리</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {effective.position_management_interval_seconds}초
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-500">재검토 확인 주기</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {effective.decision_cycle_interval_minutes}분
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-500">AI 기본 검토 간격</p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                              {effective.ai_call_interval_minutes}분
                            </p>
                          </div>
                        </div>
                        <div className="grid gap-3 lg:grid-cols-2">
                          <div className="rounded-2xl bg-slate-50 px-4 py-3">
                            <p className="text-xs text-slate-500">마지막 AI 호출 / AI 검토 기준</p>
                            <p className="mt-2 break-all text-sm font-semibold text-slate-900">
                              {formatDisplayValue(effective.last_ai_decision_at, "last_ai_decision_at")}
                            </p>
                            <p className="mt-1 break-all text-sm text-slate-700">
                              이벤트가 생기면 AI 검토를 시도합니다. 이 값은 열린 포지션 재검토 기준과 수동 재실행 보호에 함께 사용됩니다.
                            </p>
                          </div>
                          <div className="rounded-2xl bg-slate-50 px-4 py-3">
                            <p className="text-xs text-slate-500">최근 사이클 상태</p>
                            <p className="mt-2 break-all text-sm text-slate-700">
                              시장 갱신 {formatDisplayValue(effective.last_market_refresh_at, "last_market_refresh_at")}
                            </p>
                            <p className="mt-1 break-all text-sm text-slate-700">
                              포지션 관리 {formatDisplayValue(effective.last_position_management_at, "last_position_management_at")}
                            </p>
                            <p className="mt-1 break-all text-sm text-slate-700">
                              재검토 확인 {formatDisplayValue(effective.last_decision_at, "last_decision_at")}
                            </p>
                            <p className="mt-2 break-all text-sm font-semibold text-slate-900">
                              재검토 확인 주기 {effective.decision_cycle_interval_minutes}분
                            </p>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-500">저장 후 실제 주기와 마지막 실행 시각이 계산됩니다.</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </details>
    </div>
  );
}

export function CadenceSettingsPanel({
  form,
  mergedSymbols,
  overrideRows,
  effectiveCadenceBySymbol,
  adaptiveSignalSummary,
  positionManagementSummary,
  aiUsage,
  isPending,
  feedback,
  onFieldChange,
  onSymbolOverrideChange,
  onSave,
}: {
  form: CadenceForm;
  mergedSymbols: string[];
  overrideRows: SymbolCadenceOverride[];
  effectiveCadenceBySymbol: Record<string, SymbolEffectiveCadence>;
  adaptiveSignalSummary: Record<string, unknown>;
  positionManagementSummary: Record<string, unknown>;
  aiUsage: AIUsagePayload | null;
  isPending: boolean;
  feedback?: FeedbackMessage;
  onFieldChange: (field: keyof CadenceForm, value: CadenceForm[keyof CadenceForm]) => void;
  onSymbolOverrideChange: (symbol: string, patch: Partial<SymbolCadenceOverride>) => void;
  onSave: () => void;
}) {
  return (
    <div className="space-y-5">
      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">운영 주기 기본값</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            거래소 동기화, 시장 갱신, 포지션 관리, 재검토 확인 주기, AI 기본 검토 간격의 전역 기본값을 분리해 관리합니다. AI 자체는 고정 15분 정기호출이 아니라 이벤트 기반 + 주기 백스톱으로 동작합니다.
          </p>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">운영 원칙</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              거래소 동기화는 전역 공용 주기만 사용합니다. 심볼별 override는 시장 갱신, 포지션 관리, 재검토 확인 주기, AI 기본 검토 간격에만 적용됩니다.
            </p>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Field label="거래소 동기화(초)">
              <input
                className={inputClass}
                type="number"
                min={30}
                max={3600}
                value={form.exchange_sync_interval_seconds}
                onChange={(event) => onFieldChange("exchange_sync_interval_seconds", Number(event.target.value))}
              />
            </Field>
            <Field label="시장 갱신(분)">
              <input
                className={inputClass}
                type="number"
                min={1}
                max={1440}
                value={form.market_refresh_interval_minutes}
                onChange={(event) => onFieldChange("market_refresh_interval_minutes", Number(event.target.value))}
              />
            </Field>
            <Field label="포지션 관리(초)">
              <input
                className={inputClass}
                type="number"
                min={30}
                max={3600}
                value={form.position_management_interval_seconds}
                onChange={(event) => onFieldChange("position_management_interval_seconds", Number(event.target.value))}
              />
            </Field>
            <Field label="재검토 확인 주기(분)" hint="주기 cycle이 재검토 이벤트가 있는지 확인하는 기본 간격입니다.">
              <input
                className={inputClass}
                type="number"
                min={1}
                value={form.decision_cycle_interval_minutes}
                onChange={(event) => onFieldChange("decision_cycle_interval_minutes", Number(event.target.value))}
              />
            </Field>
            <Field label="AI 기본 검토 간격(분)" hint="열린 포지션 재검토 기준과 수동 재실행 보호에 사용하는 기본 간격입니다.">
              <input
                className={inputClass}
                type="number"
                min={5}
                value={form.ai_call_interval_minutes}
                onChange={(event) => onFieldChange("ai_call_interval_minutes", Number(event.target.value))}
              />
            </Field>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">보수적 운영 규칙</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            최근 성과가 나쁠 때는 보수화하고, 열린 포지션은 손절을 넓히지 않는 방향으로만 관리합니다.
          </p>
          <div className="mt-4 space-y-4">
            <Toggle
              checked={form.adaptive_signal_enabled}
              label="적응형 신호 조정 사용"
              onChange={(value) => onFieldChange("adaptive_signal_enabled", value)}
            />
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">적응형 조정 상한/하한</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                가중치{" "}
                {formatDisplayValue(
                  ((adaptiveSignalSummary as Record<string, unknown>).bounds as Record<string, unknown> | undefined)
                    ?.signal_weight_min,
                  "signal_weight",
                )}{" "}
                -{" "}
                {formatDisplayValue(
                  ((adaptiveSignalSummary as Record<string, unknown>).bounds as Record<string, unknown> | undefined)
                    ?.signal_weight_max,
                  "signal_weight",
                )}
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                최근 성과가 나쁠 때만 신뢰도와 리스크를 할인합니다. 데이터가 부족하면 중립값으로 되돌립니다.
              </p>
            </div>

            <Toggle
              checked={form.position_management_enabled}
              label="보수적 포지션 관리 사용"
              onChange={(value) => onFieldChange("position_management_enabled", value)}
            />
            <div className="grid gap-3 md:grid-cols-2">
              <Toggle
                checked={form.break_even_enabled}
                label="1R 도달 시 본절 이동"
                onChange={(value) => onFieldChange("break_even_enabled", value)}
              />
              <Toggle
                checked={form.atr_trailing_stop_enabled}
                label="ATR 트레일링 스탑"
                onChange={(value) => onFieldChange("atr_trailing_stop_enabled", value)}
              />
              <Toggle
                checked={form.partial_take_profit_enabled}
                label="부분 익절"
                onChange={(value) => onFieldChange("partial_take_profit_enabled", value)}
              />
              <Toggle
                checked={form.holding_edge_decay_enabled}
                label="보유 시간 경과 감쇠"
                onChange={(value) => onFieldChange("holding_edge_decay_enabled", value)}
              />
              <Toggle
                checked={form.reduce_on_regime_shift_enabled}
                label="레짐 전환 시 축소 강화"
                onChange={(value) => onFieldChange("reduce_on_regime_shift_enabled", value)}
              />
            </div>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">포지션 관리 규칙</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                1R 본절 이동 / ATR x{" "}
                {formatDisplayValue(
                  ((positionManagementSummary as Record<string, unknown>).fixed_parameters as
                    | Record<string, unknown>
                    | undefined)?.trailing_atr_multiple,
                )}{" "}
                트레일링 /{" "}
                {formatDisplayValue(
                  ((positionManagementSummary as Record<string, unknown>).fixed_parameters as
                    | Record<string, unknown>
                    | undefined)?.partial_take_profit_fraction,
                  "risk_pct",
                )}{" "}
                부분 익절
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {String(
                  (positionManagementSummary as Record<string, unknown>).summary ??
                    "포지션 관리는 손절을 넓히지 않습니다. 보호를 더 타이트하게 하거나 보수적 축소만 권고합니다.",
                )}
              </p>
            </div>
          </div>
        </div>
      </section>

      <SymbolCadenceOverridePanel
        mergedSymbols={mergedSymbols}
        overrideRows={overrideRows}
        effectiveCadenceBySymbol={effectiveCadenceBySymbol}
        form={form}
        onSymbolOverrideChange={onSymbolOverrideChange}
      />

      <div className="flex flex-col gap-3 rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
        <p className="text-sm text-slate-600">
          운영 설정 변경은 전체 payload로 함께 저장됩니다. 저장 전까지 즉시 실행 액션에는 반영되지 않습니다.
        </p>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <button
            className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60"
            disabled={isPending}
            onClick={onSave}
            type="button"
          >
            {isPending ? "저장 중..." : "운영 설정 저장"}
          </button>
          <div className="lg:min-w-[18rem]">
            <InlineFeedback message={feedback} />
          </div>
        </div>
      </div>

      <AIUsagePanel usage={aiUsage} />
    </div>
  );
}
