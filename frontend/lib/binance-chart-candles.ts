export type BinanceChartCandle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type BinanceChartTimeframe = "15m" | "1h" | "4h";

const binanceFuturesBaseUrl =
  process.env.BINANCE_FUTURES_PUBLIC_BASE_URL ?? "https://fapi.binance.com";

function finiteNumber(value: unknown) {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseKlineRow(row: unknown): BinanceChartCandle | null {
  if (!Array.isArray(row)) {
    return null;
  }
  const openTime = finiteNumber(row[0]);
  const open = finiteNumber(row[1]);
  const high = finiteNumber(row[2]);
  const low = finiteNumber(row[3]);
  const close = finiteNumber(row[4]);
  const volume = finiteNumber(row[5]);
  if (
    openTime === null ||
    open === null ||
    high === null ||
    low === null ||
    close === null ||
    volume === null
  ) {
    return null;
  }
  return {
    timestamp: new Date(openTime).toISOString(),
    open,
    high,
    low,
    close,
    volume,
  };
}

export async function fetchBinanceChartCandles({
  symbol,
  timeframe,
  limit,
}: {
  symbol: string;
  timeframe: BinanceChartTimeframe;
  limit: number;
}) {
  const normalizedLimit = Math.max(1, Math.min(Math.trunc(limit), 1500));
  const params = new URLSearchParams({
    symbol: symbol.toUpperCase(),
    interval: timeframe,
    limit: String(normalizedLimit),
  });

  try {
    const response = await fetch(`${binanceFuturesBaseUrl}/fapi/v1/klines?${params}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    const payload = await response.json();
    if (!Array.isArray(payload)) {
      return [];
    }
    return payload
      .map(parseKlineRow)
      .filter((item): item is BinanceChartCandle => item !== null);
  } catch {
    return [];
  }
}

export async function fetchBinanceChartCandlesBySymbol({
  symbols,
  timeframe,
  limit,
}: {
  symbols: string[];
  timeframe: BinanceChartTimeframe;
  limit: number;
}) {
  const uniqueSymbols = Array.from(new Set(symbols.map((symbol) => symbol.toUpperCase()).filter(Boolean)));
  const entries = await Promise.all(
    uniqueSymbols.map(async (symbol) => {
      const candles = await fetchBinanceChartCandles({ symbol, timeframe, limit });
      return [symbol, candles] as const;
    }),
  );
  return Object.fromEntries(entries.filter(([, candles]) => candles.length > 0));
}
