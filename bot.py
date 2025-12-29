import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
from scipy.signal import argrelextrema

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()

def get_top_100_coins():
    """Fetches top 100 non-stablecoins from CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 100, 'page': 1}
        data = requests.get(url, params=params).json()
        stables = ['usdt', 'usdc', 'dai', 'busd', 'fdusd', 'pyusd', 'usde', 'tusd', 'steth', 'wbtc']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'] not in stables]
    except:
        return []

def detect_triple_divergence(df, order=5):
    """Detects 3 consecutive diverging pivots."""
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 60: return None

    # BULLISH: Price LL (Lower Lows) | RSI HL (Higher Lows)
    # argrelextrema finds local 'pits' in the data
    low_peaks = argrelextrema(df.low.values, np.less, order=order)[0]
    if len(low_peaks) >= 3:
        # Get the last 3 pivots
        p3, p2, p1 = df.low.iloc[low_peaks[-3:]].values
        r3, r2, r1 = df.RSI.iloc[low_peaks[-3:]].values

        if p3 > p2 > p1 and r3 < r2 < r1:
            return "🚀 TRIPLE BULLISH DIV (REVERSAL UP)"

    # BEARISH: Price HH (Higher Highs) | RSI LH (Lower Highs)
    high_peaks = argrelextrema(df.high.values, np.greater, order=order)[0]
    if len(high_peaks) >= 3:
        p3, p2, p1 = df.high.iloc[high_peaks[-3:]].values
        r3, r2, r1 = df.RSI.iloc[high_peaks[-3:]].values

        if p3 < p2 < p1 and r3 > r2 > r1:
            return "🔥 TRIPLE BEARISH DIV (REVERSAL DOWN)"

    return None

def main():
    if not DISCORD_WEBHOOK:
        print("Error: Set DISCORD_WEBHOOK_URL in Secrets!")
        return

    symbols = get_top_100_coins()
    for symbol in symbols:
        try:
            # 15m timeframe, limit to 150 bars for enough pivot history
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
            signal = detect_triple_divergence(df)

            if signal:
                tv_link = f"https://www.tradingview.com/chart/?symbol=KRAKEN:{symbol.replace('/','')}"
                payload = {
                    "content": f"## {signal}\n**Symbol:** {symbol}\n**Timeframe:** 15m\n[🔍 Open Chart]({tv_link})"
                }
                requests.post(DISCORD_WEBHOOK, json=payload)
                print(f"Signal found for {symbol}")
        except:
            continue

if __name__ == "__main__":
    main()
