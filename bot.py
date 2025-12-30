import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
from scipy.signal import argrelextrema
from datetime import datetime

# --- CONFIG & DATABASE ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

# --- STRATEGY CONSTANTS ---
INITIAL_CASH = 250.0
SL_PCT, TP1_PCT, TP3_PCT = 0.015, 0.01, 0.05
PIVOT_ORDER = 4 

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

def detect_signal(df, order=PIVOT_ORDER):
    # Force data to numeric to prevent math errors
    prices = pd.to_numeric(df['close']).values
    df['RSI'] = ta.rsi(df['close'], length=14)
    rsi_vals = df['RSI'].values
    
    # Remove NaN from RSI for accurate pivot hunting
    valid_idx = ~np.isnan(rsi_vals)
    prices = prices[valid_idx]
    rsi_vals = rsi_vals[valid_idx]
    
    if len(prices) < 50: return None
    
    # LONG: 3 Close Lower Lows + 3 RSI Higher Lows
    low_idx = argrelextrema(prices, np.less, order=order)[0]
    if len(low_idx) >= 3:
        p = prices[low_idx[-3:]]
        r = rsi_vals[low_idx[-3:]]
        if (p[0] > p[1] > p[2]) and (r[0] < r[1] < r[2]):
            return "LONG"

    # SHORT: 3 Close Higher Highs + 3 RSI Lower Highs
    high_idx = argrelextrema(prices, np.greater, order=order)[0]
    if len(high_idx) >= 3:
        p = prices[high_idx[-3:]]
        r = rsi_vals[high_idx[-3:]]
        if (p[0] < p[1] < p[2]) and (r[0] > r[1] > r[2]):
            return "SHORT"
            
    return None

def monitor(db):
    print(f"Checking {len(db['active_trades'])} active trades...")
    for sym, t in list(db['active_trades'].items()):
        try:
            price = EXCHANGE.fetch_ticker(sym)['last']
            is_long = t['side'] == "LONG"
            if (is_long and price <= t['sl']) or (not is_long and price >= t['sl']):
                db['balance'] -= t['size'] * SL_PCT
                requests.post(DISCORD_WEBHOOK, json={"content": f"🏁 {sym}: ❌ STOP LOSS. Balance: ${db['balance']:.2f}"})
                del db['active_trades'][sym]
            elif (is_long and price >= t['tp3']) or (not is_long and price <= t['tp3']):
                db['balance'] += t['size'] * TP3_PCT
                requests.post(DISCORD_WEBHOOK, json={"content": f"🏁 {sym}: 💰 FULL TP. Balance: ${db['balance']:.2f}"})
                del db['active_trades'][sym]
            elif not t['tp1_hit'] and ((is_long and price >= t['tp1']) or (not is_long and price <= t['tp1'])):
                t['tp1_hit'], t['sl'] = True, t['entry']
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ {sym}: TP1 hit. SL to entry."})
        except: continue

def main():
    print(f"--- Scan Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    db = load_db()
    monitor(db)
    risk_amt = max(db['balance'], 250.0) * 0.03
    
    symbols = get_symbols()
    total = len(symbols)
    print(f"Scanning {total} symbols...")
    
    for i, sym in enumerate(symbols, 1):
        try:
            # Fetch data with error handling
            ohlcv = EXCHANGE.fetch_ohlcv(sym, '15m', limit=200)
            if not ohlcv: continue
            
            df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            # Debugging line: ensure the scanner sees data
            if i == 1: print(f"Sample data for {sym}: Close {df['c'].iloc[-1]}")
            
            sig = detect_signal(df)
            if sig:
                print(f"✨ {sig} FOUND on {sym}")
                ent = float(df['c'].iloc[-1])
                mult = 1 if sig=="LONG" else -1
                db['active_trades'][sym] = {
                    "side": sig, "entry": ent, "tp1_hit": False, "size": risk_amt,
                    "sl": ent - (ent * SL_PCT * mult),
                    "tp1": ent + (ent * TP1_PCT * mult),
                    "tp3": ent + (ent * TP3_PCT * mult)
                }
                requests.post(DISCORD_WEBHOOK, json={"content": f"# 🔔 NEW {sig}\n**Asset:** {sym}\n**Entry:** ${ent:,.4f}"})
            
            # Print progress every 10 coins to keep logs clean but active
            if i % 10 == 0: print(f"Progress: {i}/{total} scanned...")
                
        except Exception: continue
            
    save_db(db)
    print(f"--- Scan Finished. Balance: ${db['balance']:.2f} ---")

if __name__ == "__main__": main()
