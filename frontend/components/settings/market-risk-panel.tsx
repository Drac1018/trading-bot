"use client";

import { Field, inputClass } from "./form-primitives";

type MarketRiskForm = {
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
};

export function MarketRiskPanel({
  form,
  mergedSymbols,
  symbolOptions,
  onFieldChange,
  onToggleTrackedSymbol,
}: {
  form: MarketRiskForm;
  mergedSymbols: string[];
  symbolOptions: string[];
  onFieldChange: (field: keyof MarketRiskForm, value: MarketRiskForm[keyof MarketRiskForm]) => void;
  onToggleTrackedSymbol: (symbol: string) => void;
}) {
  return (
    <section className="grid gap-5 xl:grid-cols-2">
      <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
        <h3 className="text-lg font-semibold text-slate-900">시장 / 리스크</h3>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          심볼 구성, 기본 시장 타임프레임, 손실 한도와 같은 전역 입력 기준을 이 영역에서 관리합니다.
        </p>
        <div className="mt-4 space-y-4">
          <Field label="기본 심볼">
            <select
              className={inputClass}
              value={form.default_symbol}
              onChange={(event) => onFieldChange("default_symbol", event.target.value)}
            >
              {mergedSymbols.map((symbol) => (
                <option key={symbol} value={symbol}>
                  {symbol}
                </option>
              ))}
            </select>
          </Field>

          <div>
            <p className="text-sm font-semibold text-slate-900">추적 심볼</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {symbolOptions.map((symbol) => {
                const active = form.tracked_symbols.includes(symbol);
                return (
                  <button
                    key={symbol}
                    className={`rounded-full px-4 py-2 text-sm font-semibold ${
                      active ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-700"
                    }`}
                    onClick={() => onToggleTrackedSymbol(symbol)}
                    type="button"
                  >
                    {symbol}
                  </button>
                );
              })}
            </div>
          </div>

          <Field label="사용자 지정 심볼" hint="쉼표로 구분하면 추적 심볼 목록에 함께 합쳐집니다.">
            <input
              className={inputClass}
              value={form.custom_symbols}
              onChange={(event) => onFieldChange("custom_symbols", event.target.value.toUpperCase())}
              placeholder="APTUSDT, AVAXUSDT"
            />
          </Field>

          <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 심볼 집합</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{mergedSymbols.join(", ")}</p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Field label="기본 시장 타임프레임" hint="AI 호출 주기가 아니라 캔들/시장 기준 타임프레임입니다.">
              <input
                className={inputClass}
                value={form.default_timeframe}
                onChange={(event) => onFieldChange("default_timeframe", event.target.value)}
              />
            </Field>
            <Field label="최대 레버리지" hint="런타임 하드 상한은 5x로 유지됩니다.">
              <input
                className={inputClass}
                type="number"
                min={1}
                max={5}
                step="0.1"
                value={form.max_leverage}
                onChange={(event) => onFieldChange("max_leverage", Number(event.target.value))}
              />
            </Field>
            <Field label="거래당 최대 리스크" hint="런타임 하드 상한은 2%로 유지됩니다.">
              <input
                className={inputClass}
                type="number"
                min={0.001}
                max={0.02}
                step="0.001"
                value={form.max_risk_per_trade}
                onChange={(event) => onFieldChange("max_risk_per_trade", Number(event.target.value))}
              />
            </Field>
            <Field label="일일 최대 손실" hint="런타임 하드 상한은 5%로 유지됩니다.">
              <input
                className={inputClass}
                type="number"
                min={0.001}
                max={0.05}
                step="0.001"
                value={form.max_daily_loss}
                onChange={(event) => onFieldChange("max_daily_loss", Number(event.target.value))}
              />
            </Field>
            <Field label="최대 연속 손실">
              <input
                className={inputClass}
                type="number"
                min={1}
                max={20}
                value={form.max_consecutive_losses}
                onChange={(event) => onFieldChange("max_consecutive_losses", Number(event.target.value))}
              />
            </Field>
            <Field label="시장 데이터 최신도 한계(초)">
              <input
                className={inputClass}
                type="number"
                min={30}
                value={form.stale_market_seconds}
                onChange={(event) => onFieldChange("stale_market_seconds", Number(event.target.value))}
              />
            </Field>
            <Field label="슬리피지 임계값">
              <input
                className={inputClass}
                type="number"
                min={0.0001}
                max={0.1}
                step="0.0001"
                value={form.slippage_threshold_pct}
                onChange={(event) => onFieldChange("slippage_threshold_pct", Number(event.target.value))}
              />
            </Field>
          </div>
        </div>
      </div>
    </section>
  );
}
