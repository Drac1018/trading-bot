export type BinanceChartCandle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type BinanceChartTimeframe = "15m" | "1h" | "4h";

type BinanceKlineCacheEntry = {
  candles: BinanceChartCandle[];
  expiresAt: number;
  pending?: Promise<BinanceChartCandle[]>;
};

const binanceFuturesBaseUrl =
  process.env.BINANCE_FUTURES_PUBLIC_BASE_URL ?? "https://fapi.binance.com";
const configuredBinanceKlineRevalidateSeconds = Number(process.env.BINANCE_KLINE_REVALIDATE_SECONDS ?? "30");
const binanceKlineRevalidateSeconds = Number.isFinite(configuredBinanceKlineRevalidateSeconds)
  ? Math.max(5, Math.min(Math.trunc(configuredBinanceKlineRevalidateSeconds), 300))
  : 30;
const binanceKlineCache = new Map<string, BinanceKlineCacheEntry>();
const binanceKlineCacheMaxEntries = 100;

function trimBinanceKlineCache() {
  if (binanceKlineCache.size <= binanceKlineCacheMaxEntries) {
    return;
  }
  const now = Date.now();
  for (const [key, entry] of binanceKlineCache.entries()) {
    if (entry.expiresAt <= now && !entry.pending) {
      binanceKlineCache.delete(key);
    }
  }
  while (binanceKlineCache.size > binanceKlineCacheMaxEntries) {
    const oldestKey = binanceKlineCache.keys().next().value;
    if (!oldestKey) {
      return;
    }
    binanceKlineCache.delete(oldestKey);
  }
}

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
  const cacheKey = params.toString();
  const now = Date.now();
  const cached = binanceKlineCache.get(cacheKey);
  if (cached && cached.expiresAt > now) {
    return cached.candles;
  }
  if (cached?.pending) {
    try {
      return await cached.pending;
    } catch {
      return cached.candles;
    }
  }

  const pending = (async () => {
    const response = await fetch(`${binanceFuturesBaseUrl}/fapi/v1/klines?${params}`, {
      next: { revalidate: binanceKlineRevalidateSeconds },
    });
    if (!response.ok) {
      throw new Error(`Binance kline request failed with ${response.status}`);
    }
    const payload = await response.json();
    if (!Array.isArray(payload)) {
      throw new Error("Binance kline response was not an array");
    }
    return payload
      .map(parseKlineRow)
      .filter((item): item is BinanceChartCandle => item !== null);
  })();
  binanceKlineCache.set(cacheKey, {
    candles: cached?.candles ?? [],
    expiresAt: now + binanceKlineRevalidateSeconds * 1000,
    pending,
  });
  trimBinanceKlineCache();
  try {
    const candles = await pending;
    binanceKlineCache.set(cacheKey, {
      candles,
      expiresAt: Date.now() + binanceKlineRevalidateSeconds * 1000,
    });
    return candles;
  } catch {
    binanceKlineCache.delete(cacheKey);
    return cached?.candles ?? [];
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
