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
        # Filter out stables and pegged assets
        stables = ['usdt', 'usdc', 'dai', 'busd', 'fdusd', 'pyusd', 'usde', 'tusd', 'steth', 'wbtc', 'weth']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in stables]
    except Exception as e:
        print(f"CoinGecko API Error: {e}")
        return []

def detect_triple_divergence(df, order=5):
    """Detects 3 consecutive diverging pivots (Triple Divergence)."""
    # Calculate RSI using pandas_ta
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    
    if len(df) < 60: 
        return None

    # --- BULLISH DIVERGENCE (Price Lower Lows / RSI Higher Lows) ---
    low_peaks = argrelextrema(df.low.values, np.less, order=order)[0]
    if len(low_peaks) >= 3:
        # Get the last 3 price pivots and their RSI values
        p3, p2, p1 = df.low.iloc[low_peaks[-3:]].values
        r3, r2, r1 = df.RSI.iloc[low_peaks[-3:]].values
        
        # Logic: Price is dropping (or flat), RSI is rising
        if p3 >= p2 >= p1 and r3 < r2 < r1:
            return "🚀 TRIPLE BULLISH DIVERGENCE"

    # --- BEARISH DIVERGENCE (Price Higher Highs / RSI Lower Highs) ---
    high_peaks = argrelextrema(df.high.values, np.greater, order=order)[0]
    if len(high_peaks) >= 3:
        # Get the last 3 price pivots and their RSI values
        p3, p2, p1 = df.high.iloc[high_peaks[-3:]].values
        r3, r2, r1 = df.RSI.iloc[high_peaks[-3:]].values
        
        # Logic: Price is rising (or flat), RSI is dropping
        if p3 <= p2 <= p1 and r3 > r2 > r1:
            return "🔥 TRIPLE BEARISH DIVERGENCE"
            
    return None

def main():
    if not DISCORD_WEBHOOK:
        print("CRITICAL ERROR: DISCORD_WEBHOOK_URL not found in GitHub Secrets!")
        return

    print("Starting Scan...")
    symbols = get_top_100_coins()
    
    for symbol in symbols:
        try:
            # Fetch 150 candles to ensure we have enough history for 3 pivots
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
            
            signal = detect_triple_divergence(df)
            
            if signal:
                tv_link = f"https://www.tradingview.com/chart/?symbol=KRAKEN:{symbol.replace('/','')}"
                payload = {
                    "content": f"## {signal}\n**Asset:** {symbol}\n**Timeframe:** 15m\n[🔍 View on TradingView]({tv_link})"
                }
                requests.post(DISCORD_WEBHOOK, json=payload)
                print(f"ALERT: {signal} found for {symbol}")
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
            continue
    print("Scan Complete.")

if __name__ == "__main__":
    main()
