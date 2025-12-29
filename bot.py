import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
from scipy.signal import argrelextrema
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

# --- TRADING RULES ---
INITIAL_BALANCE = 250.0
RISK_PCT = 0.03  # 3% of portfolio per trade
SL_PCT = 0.015   # 1.5% Stop Loss
TP1_PCT = 0.01   # 1% TP (Sell 15% & Move SL to Entry)
TP2_PCT = 0.03   # 3% TP (Sell 50% of remainder)
TP3_PCT = 0.05   # 5% TP (Close all)

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {"balance": INITIAL_BALANCE, "active_trades": {}, "history": []}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_top_100():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 100, 'page': 1}
        data = requests.get(url, params=params).json()
        stables = ['usdt', 'usdc', 'dai', 'busd', 'fdusd', 'pyusd', 'usde', 'tusd', 'steth', 'wbtc', 'weth']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in stables]
    except:
        return []

def detect_quad_divergence(df, order=5):
    """Detects 4-point divergence."""
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 100: return None

    # LONG (4 Lower Lows Price / 4 Higher Lows RSI)
    lows = argrelextrema(df.low.values, np.less, order=order)[0]
    if len(lows) >= 4:
        p, r = df.low.iloc[lows[-4:]].values, df.RSI.iloc[lows[-4:]].values
        if p[0] > p[1] > p[2] > p[3] and r[0] < r[1] < r[2] < r[3]:
            return "LONG"

    # SHORT (4 Higher Highs Price / 4 Lower Highs RSI)
    highs = argrelextrema(df.high.values, np.greater, order=order)[0]
    if len(highs) >= 4:
        p, r = df.high.iloc[highs[-4:]].values, df.RSI.iloc[highs[-4:]].values
        if p[0] < p[1] < p[2] < p[3] and r[0] > r[1] > r[2] > r[3]:
            return "SHORT"
    return None

def monitor_trades(db):
    """Checks active trades against current prices."""
    active = db['active_trades']
    finished = []
    
    for symbol, t in list(active.items()):
        try:
            ticker = EXCHANGE.fetch_ticker(symbol)
            curr = ticker['last']
            
            # --- LONG LOGIC ---
            if t['side'] == "LONG":
                if curr <= t['sl']: # Stop Loss Hit
                    db['balance'] -= t['size'] * SL_PCT
                    finished.append((symbol, "❌ STOP LOSS HIT"))
                elif curr >= t['tp3']: # Final TP
                    db['balance'] += t['size'] * TP3_PCT
                    finished.append((symbol, "💰 FULL TAKE PROFIT HIT"))
                elif curr >= t['tp1'] and not t['tp1_hit']:
                    t['tp1_hit'] = True
                    t['sl'] = t['entry'] # Move SL to Entry
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✅ {symbol}: TP1 Hit! Moving SL to Entry."})
            
            # --- SHORT LOGIC ---
            elif t['side'] == "SHORT":
                if curr >= t['sl']:
                    db['balance'] -= t['size'] * SL_PCT
                    finished.append((symbol, "❌ STOP LOSS HIT"))
                elif curr <= t['tp3']:
                    db['balance'] += t['size'] * TP3_PCT
                    finished.append((symbol, "💰 FULL TAKE PROFIT HIT"))
                elif curr <= t['tp1'] and not t['tp1_hit']:
                    t['tp1_hit'] = True
                    t['sl'] = t['entry']
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✅ {symbol}: TP1 Hit! Moving SL to Entry."})

        except: continue

    for sym, msg in finished:
        del db['active_trades'][sym]
        db['history'].append({"symbol": sym, "result": msg, "time": str(datetime.now())})
        requests.post(DISCORD_WEBHOOK, json={"content": f"🏁 {sym}: {msg}\nNew Balance: ${db['balance']:.2f}"})

def main():
    db = load_db()
    # Update portfolio sizing logic
    if db['balance'] >= 500: # 2x of initial $250
        risk_amount = db['balance'] * 0.03
    else:
        risk_amount = 250 * 0.03
    
    monitor_trades(db)
    symbols = get_top_100()
    
    for symbol in symbols:
        if symbol in db['active_trades']: continue
        
        try:
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            signal = detect_quad_divergence(df)
            
            if signal:
                entry = df['c'].iloc[-1]
                sl = entry * (1-SL_PCT) if signal=="LONG" else entry * (1+SL_PCT)
                tp1 = entry * (1+TP1_PCT) if signal=="LONG" else entry * (1-TP1_PCT)
                tp3 = entry * (1+TP3_PCT) if signal=="LONG" else entry * (1-TP3_PCT)
                
                db['active_trades'][symbol] = {
                    "side": signal, "entry": entry, "sl": sl, "tp1": tp1, 
                    "tp3": tp3, "tp1_hit": False, "size": risk_amount
                }
                
                msg = f"# 🔔 NEW {signal} TRADE\n**Asset:** {symbol}\n**Entry:** ${entry:,.4f}\n**SL:** ${sl:,.4f}\n**TP3:** ${tp3:,.4f}"
                requests.post(DISCORD_WEBHOOK, json={"content": msg})
        except: continue
    
    save_db(db)

if __name__ == "__main__":
    main()
