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

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return {"balance": INITIAL_CASH, "active_trades": {}, "history": []}

def save_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

def get_symbols():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100"
        data = requests.get(url).json()
        stables = ['usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth', 'usds', 'gusd']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in stables]
    except: return []

def detect_signal(df, order=5):
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 100: return None
    
    # LONG: 3 Candle Close Lower Lows + 3 RSI Higher Lows
    lows = argrelextrema(df.close.values, np.less, order=order)[0]
    if len(lows) >= 3:
        p = df.close.iloc[lows[-3:]].values
        r = df.RSI.iloc[lows[-3:]].values
        if (p[0] > p[1] > p[2]) and (r[0] < r[1] < r[2]):
            return "LONG"

    # SHORT: 3 Candle Close Higher Highs + 3 RSI Lower Highs
    highs = argrelextrema(df.close.values, np.greater, order=order)[0]
    if len(highs) >= 3:
        p = df.close.iloc[highs[-3:]].values
        r = df.RSI.iloc[highs[-3:]].values
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
                requests.post(DISCORD_WEBHOOK, json={"content": f"🏁 {sym}: ❌ STOP LOSS HIT. Balance: ${db['balance']:.2f}"})
                del db['active_trades'][sym]
            elif (is_long and price >= t['tp3']) or (not is_long and price <= t['tp3']):
                db['balance'] += t['size'] * TP3_PCT
                requests.post(DISCORD_WEBHOOK, json={"content": f"🏁 {sym}: 💰 FULL TP HIT. Balance: ${db['balance']:.2f}"})
                del db['active_trades'][sym]
            elif not t['tp1_hit'] and ((is_long and price >= t['tp1']) or (not is_long and price <= t['tp1'])):
                t['tp1_hit'], t['sl'] = True, t['entry']
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ {sym}: TP1 hit. SL moved to entry."})
        except: continue

def main():
    print(f"--- Scan Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    db = load_db()
    monitor(db)
    risk_amt = max(db['balance'], 250.0) * 0.03 if db['balance'] < 500 else db['balance'] * 0.03
    symbols = get_symbols()
    total = len(symbols)
    print(f"Scanning {total} symbols...")
    
    for i, sym in enumerate(symbols, 1):
        # This will show you exactly which coin is being checked in the GitHub log
        print(f"[{i}/{total}] Checking {sym}...", end='\r')
        
        if sym in db['active_trades']: continue
        try:
            df = pd.DataFrame(EXCHANGE.fetch_ohlcv(sym, '15m', limit=150), columns=['t','o','h','l','c','v'])
            sig = detect_signal(df)
            if sig:
                print(f"\n✨ MATCH FOUND: {sig} on {sym}")
                ent = df['c'].iloc[-1]
                mult = 1 if sig=="LONG" else -1
                db['active_trades'][sym] = {
                    "side": sig, "entry": ent, "tp1_hit": False, "size": risk_amt,
                    "sl": ent - (ent * SL_PCT * mult),
                    "tp1": ent + (ent * TP1_PCT * mult),
                    "tp3": ent + (ent * TP3_PCT * mult)
                }
                requests.post(DISCORD_WEBHOOK, json={"content": f"# 🔔 NEW {sig} TRADE\n**Asset:** {sym}\n**Entry:** ${ent:,.4f}\n**SL:** ${db['active_trades'][sym]['sl']:,.4f}"})
        except: continue
        
    save_db(db)
    print(f"\n--- Scan Finished. Current Portfolio: ${db['balance']:.2f} ---")

if __name__ == "__main__": main()
