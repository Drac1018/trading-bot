export const ALL_SYMBOLS = "ALL";

export type SelectedSymbolMode = "all" | "single";

type ResolveSelectedSymbolOptions = {
  mode?: SelectedSymbolMode;
};

function normalizeSymbol(value: string | null | undefined) {
  return value?.trim().toUpperCase() ?? "";
}

function normalizeTrackedSymbols(trackedSymbols: string[]) {
  return [...new Set(trackedSymbols.map((item) => normalizeSymbol(item)).filter(Boolean))];
}

export function resolveSelectedSymbol(
  requested: string | null,
  trackedSymbols: string[],
  defaultSymbol: string,
  options: ResolveSelectedSymbolOptions = {},
) {
  const mode = options.mode ?? "all";
  const symbols = normalizeTrackedSymbols(trackedSymbols);
  const normalizedDefault = normalizeSymbol(defaultSymbol);

  if (requested) {
    const normalizedRequested = normalizeSymbol(requested);
    if (mode === "all" && normalizedRequested === ALL_SYMBOLS) {
      return ALL_SYMBOLS;
    }
    if (symbols.includes(normalizedRequested)) {
      return normalizedRequested;
    }
  }

  if (symbols.length === 0) {
    return mode === "single" && normalizedDefault ? normalizedDefault : ALL_SYMBOLS;
  }

  if (symbols.length === 1) {
    return symbols[0];
  }

  if (mode === "single") {
    if (symbols.includes(normalizedDefault)) {
      return normalizedDefault;
    }
    return symbols[0];
  }

  return ALL_SYMBOLS;
}

export function filterSymbolsBySelection<T extends { symbol: string }>(items: T[], selectedSymbol: string) {
  if (selectedSymbol === ALL_SYMBOLS) {
    return items;
  }
  return items.filter((item) => normalizeSymbol(item.symbol) === normalizeSymbol(selectedSymbol));
}

export function getSelectedSymbolPolicyHint(mode: SelectedSymbolMode) {
  if (mode === "single") {
    return "이 화면은 단일 심볼 판단 상세 화면이라 첫 진입 시 기본 심볼을 자동 선택합니다.";
  }
  return "이 화면은 멀티 심볼 비교 화면이라 첫 진입 시 전체 심볼을 기본으로 보여줍니다.";
}
