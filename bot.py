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
PIVOT_ORDER = 1

def log(msg):
    """Force immediate logging to GitHub console"""
    print(msg)
    sys.stdout.flush()

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return {"balance": INITIAL_CASH, "active_trades": {}, "history": []}

def save_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

def get_symbols():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=150"
        data = requests.get(url).json()
        excluded = [
            'usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth', 
            'usds', 'gusd', 'wsteth', 'wbeth', 'weeth', 'cbbtc', 'usdt0', 'susds', 
            'susde', 'usd1', 'syrupusdc', 'usdf', 'jitosol', 'usdg', 'rlusd', 
            'bfusd', 'bnsol', 'reth', 'wbnb', 'rseth', 'fbtc', 'lbtc',
            'gteth', 'tusd', 'tbtc', 'eutbl', 'usd0', 'oseth', 'geth',
            'solvbtc', 'usdtb', 'usdd', 'lseth', 'ustb', 'usdc.e', 'usdy', 
            'clbtc', 'meth', 'usdai', 'ezeth', 'jupsol'
        ]
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in excluded]
    except: return []

def detect_signal(df, symbol, order=PIVOT_ORDER):
    prices = pd.to_numeric(df['close']).values
    df['RSI'] = ta.rsi(df['close'], length=14)
    rsi_vals = df['RSI'].values
    valid_idx = ~np.isnan(rsi_vals)
    prices, rsi_vals = prices[valid_idx], rsi_vals[valid_idx]
    
    if len(prices) < 50: return None
    
    # BULLISH DIVERGENCE (Price Lower Lows + RSI Higher Lows)
    low_idx = argrelextrema(prices, np.less, order=order)[0]
    if len(low_idx) >= 3:
        p, r = prices[low_idx[-3:]], rsi_vals[low_idx[-3:]]
        # LOGS FOR DEBUGGING
        log(f"   [🔍] {symbol} Pivot Lows: Price={p.tolist()}, RSI={r.tolist()}")
        if (p[0] > p[1] > p[2]) and (r[0] < r[1] < r[2]): return "LONG"

    # BEARISH DIVERGENCE (Price Higher Highs + RSI Lower Highs)
    high_idx = argrelextrema(prices, np.greater, order=order)[0]
    if len(high_idx) >= 3:
        p, r = prices[high_idx[-3:]], rsi_vals[high_idx[-3:]]
        log(f"   [🔍] {symbol} Pivot Highs: Price={p.tolist()}, RSI={r.tolist()}")
        if (p[0] < p[1] < p[2]) and (r[0] > r[1] > r[2]): return "SHORT"
    
    return None

def main():
    log(f"--- SCAN START: {datetime.now()} ---")
    db = load_db()
    symbols = get_symbols()
    total = len(symbols)
    log(f"Targeting {total} coins...")
    
    for i, sym in enumerate(symbols, 1):
        log(f"[{i}/{total}] Checking {sym}...")
        try:
            ohlcv = EXCHANGE.fetch_ohlcv(sym, '15m', limit=200)
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            sig = detect_signal(df, sym)
            
            if sig:
                log(f"🚨 SIGNAL DETECTED: {sig} on {sym}")
                ent = float(df['c'].iloc[-1])
                mult = 1 if sig=="LONG" else -1
                db['active_trades'][sym] = {
                    "side": sig, "entry": ent, "tp1_hit": False, "size": 7.5,
                    "sl": ent - (ent * SL_PCT * mult),
                    "tp1": ent + (ent * TP1_PCT * mult),
                    "tp3": ent + (ent * TP3_PCT * mult)
                }
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sig} Signal**\nAsset: {sym}\nEntry: {ent}"})
        except Exception as e:
            continue
            
    save_db(db)
    log(f"--- SCAN FINISHED ---")

if __name__ == "__main__": main()
