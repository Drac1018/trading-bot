export const ALL_SYMBOLS = "ALL";

export function resolveSelectedSymbol(
  requested: string | null,
  trackedSymbols: string[],
  defaultSymbol: string,
) {
  const symbols = trackedSymbols.map((item) => item.toUpperCase());
  if (requested) {
    const normalized = requested.toUpperCase();
    if (normalized === ALL_SYMBOLS || symbols.includes(normalized)) {
      return normalized;
    }
  }
  if (symbols.length <= 1 && symbols[0]) {
    return symbols[0];
  }
  if (symbols.includes(defaultSymbol.toUpperCase())) {
    return ALL_SYMBOLS;
  }
  return ALL_SYMBOLS;
}

export function filterSymbolsBySelection<T extends { symbol: string }>(items: T[], selectedSymbol: string) {
  if (selectedSymbol === ALL_SYMBOLS) {
    return items;
  }
  return items.filter((item) => item.symbol.toUpperCase() === selectedSymbol.toUpperCase());
}
