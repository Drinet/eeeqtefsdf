import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
from scipy.signal import argrelextrema
from datetime import datetime
import sys

# --- CONFIG & DATABASE ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

# --- STRATEGY CONSTANTS ---
INITIAL_CASH = 250.0
SL_PCT, TP1_PCT, TP3_PCT = 0.015, 0.01, 0.05
PIVOT_ORDER = 1  # Extreme sensitivity

def log(msg):
    print(msg)
    sys.stdout.flush()

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"balance": INITIAL_CASH, "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

def get_symbols():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=150"
        data = requests.get(url).json()
        excluded = ['usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth', 'usds', 'gusd', 'wsteth', 'wbeth', 'weeth', 'cbbtc', 'usdt0', 'susds', 'susde', 'usd1', 'syrupusdc', 'usdf', 'jitosol', 'usdg', 'rlusd', 'bfusd', 'bnsol', 'reth', 'wbnb', 'rseth', 'fbtc', 'lbtc', 'gteth', 'tusd', 'tbtc', 'eutbl', 'usd0', 'oseth', 'geth', 'solvbtc', 'usdtb', 'usdd', 'lseth', 'ustb', 'usdc.e', 'usdy', 'clbtc', 'meth', 'usdai', 'ezeth', 'jupsol']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in excluded]
    except: return []

def detect_signal(df, symbol, order=PIVOT_ORDER):
    # Ensure we have enough data
    if len(df) < 50: return None
    
    # Calculate RSI and clean data
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna(subset=['RSI']).reset_index(drop=True)
    
    prices = df['close'].values
    rsi_vals = df['RSI'].values
    
    # BULLISH DIVERGENCE (Price Lower Lows + RSI Higher Lows)
    # We now only require the LAST 2 pivots instead of 3.
    low_idx = argrelextrema(prices, np.less, order=order)[0]
    if len(low_idx) >= 2:
        p_lows = prices[low_idx[-2:]]
        r_lows = rsi_vals[low_idx[-2:]]
        # Condition: Price going down, RSI going up
        if p_lows[0] > p_lows[1] and r_lows[0] < r_lows[1]:
            return "LONG"

    # BEARISH DIVERGENCE (Price Higher Highs + RSI Lower Highs)
    high_idx = argrelextrema(prices, np.greater, order=order)[0]
    if len(high_idx) >= 2:
        p_highs = prices[high_idx[-2:]]
        r_highs = rsi_vals[high_idx[-2:]]
        # Condition: Price going up, RSI going down
        if p_highs[0] < p_highs[1] and r_highs[0] > r_highs[1]:
            return "SHORT"
    
    return None

def main():
    log(f"--- STARTING SCAN: {datetime.now().strftime('%H:%M:%S')} ---")
    db = load_db()
    symbols = get_symbols()
    
    # Force log the start
    log(f"Found {len(symbols)} coins to check.")
    
    found_any = False
    for i, sym in enumerate(symbols, 1):
        # Progress log
        if i % 10 == 0: log(f"Progress: {i}/{len(symbols)} coins scanned...")
        
        try:
            # Fetch data (15m timeframe)
            ohlcv = EXCHANGE.fetch_ohlcv(sym, '15m', limit=100)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            
            signal = detect_signal(df, sym)
            if signal:
                log(f"✅ MATCH FOUND: {signal} on {sym}")
                found_any = True
                # Trade logic here (omitted for brevity but kept in your file)
                # ... [Internal database/discord code] ...
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{signal}** on {sym} detected at ${df['c'].iloc[-1]}"})
        except:
            continue
            
    if not found_any:
        log("No signals found in this 15m window.")
    
    save_db(db)
    log(f"--- SCAN FINISHED ---")

if __name__ == "__main__":
    main()
