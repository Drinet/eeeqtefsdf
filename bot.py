import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
import sys
from scipy.signal import argrelextrema

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

def log(msg):
    print(msg, flush=True)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                db = json.load(f)
                db.setdefault("wins", 0)
                db.setdefault("losses", 0)
                db.setdefault("active_trades", {})
                return db
        except: pass
    return {"wins": 0, "losses": 0, "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_top_coins():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 200, 'page': 1}
        data = requests.get(url, params=params).json()
        excluded = [
            'usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth', 
            'usds', 'gusd', 'wsteth', 'wbeth', 'weeth', 'cbbtc', 'usdt0', 'susds', 
            'susde', 'usd1', 'syrupusdc', 'usdf', 'jitosol', 'usdg', 'rlusd', 
            'bfusd', 'bnsol', 'reth', 'wbnb', 'rseth', 'fbtc', 'lbtc',
            'gteth', 'tusd', 'tbtc', 'eutbl', 'usd0', 'oseth', 'geth',
            'solvbtc', 'usdtb', 'usdd', 'lseth', 'ustb', 'usdc.e', 'usdy', 
            'clbtc', 'meth', 'usdai', 'ezeth', 'jupsol'
        ]
        filtered = [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in excluded]
        return filtered[:120]
    except:
        return []

def detect_triple_divergence(df, order=4):
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 100: return None

    # BULLISH (Bodies only)
    low_pivots = argrelextrema(df['close'].values, np.less, order=order)[0]
    if len(low_pivots) >= 3:
        p1, p2, p3 = df['close'].iloc[low_pivots[-3:]].values
        r1, r2, r3 = df['RSI'].iloc[low_pivots[-3:]].values
        if p1 > p2 > p3 and r1 < r2 < r3:
            return "Long trade"

    # BEARISH (Bodies only)
    high_pivots = argrelextrema(df['close'].values, np.greater, order=order)[0]
    if len(high_pivots) >= 3:
        p1, p2, p3 = df['close'].iloc[high_pivots[-3:]].values
        r1, r2, r3 = df['RSI'].iloc[high_pivots[-3:]].values
        if p1 < p2 < p3 and r1 > r2 > r3:
            return "Short trade"
    return None

def update_trades(db):
    active = db['active_trades']
    if not active: return
    
    for sym in list(active.keys()):
        try:
            t = active[sym]
            ticker = EXCHANGE.fetch_ticker(sym)
            curr = ticker['last']
            side = t['side']
            is_long = (side == "Long trade")
            
            # 1. CHECK TAKE PROFITS FIRST
            # TP3
            if (is_long and curr >= t['tp3']) or (not is_long and curr <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 HIT (5%)!** Trade closed at maximum target."})
                del active[sym]
                continue
            
            # TP2
            if not t.get('tp2_hit', False):
                if (is_long and curr >= t['tp2']) or (not is_long and curr <= t['tp2']):
                    t['tp2_hit'] = True
                    requests.post(DISCORD_WEBHOOK, json={"content": f"🎯 **{sym} TP2 HIT (3%)!** Halfway to the moon."})

            # TP1
            if not t.get('tp1_hit', False):
                if (is_long and curr >= t['tp1']) or (not is_long and curr <= t['tp1']):
                    t['tp1_hit'] = True
                    t['sl'] = t['entry'] # Move SL to entry
                    db['wins'] += 1 # Counts as a win
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 HIT (1.5%)!** SL moved to entry. Win tracked! 📈"})

            # 2. CHECK STOP LOSS (ONLY IF TP1 NOT HIT OR IF RETRACED TO ENTRY)
            if (is_long and curr <= t['sl']) or (not is_long and curr >= t['sl']):
                # If TP1 was already hit, it's just a neutral exit, not a loss
                if t.get('tp1_hit', False):
                    requests.post(DISCORD_WEBHOOK, json={"content": f"⚠️ **{sym} Closed at Entry** after hitting TP1."})
                else:
                    db['losses'] += 1
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL Hit** at {curr}. (Loss tracked)"})
                del active[sym]

        except Exception as e:
            log(f"Error updating {sym}: {e}")

def main():
    if not DISCORD_WEBHOOK:
        log("Error: Set DISCORD_WEBHOOK_URL")
        return

    db = load_db()
    update_trades(db)
    
    symbols = get_top_coins()
    log(f"Starting scan for {len(symbols)} coins...")

    for i, symbol in enumerate(symbols, 1):
        if symbol in db['active_trades']:
            continue

        try:
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=200)
            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
            signal = detect_triple_divergence(df, order=4)
            
            if signal:
                entry = float(df['close'].iloc[-1])
                mult = 1 if signal == "Long trade" else -1
                
                t_data = {
                    "side": signal,
                    "entry": entry,
                    "sl": entry * (1 - (0.02 * mult)),
                    "tp1": entry * (1 + (0.015 * mult)),
                    "tp2": entry * (1 + (0.03 * mult)),
                    "tp3": entry * (1 + (0.05 * mult)),
                    "tp1_hit": False,
                    "tp2_hit": False
                }

                db['active_trades'][symbol] = t_data

                total = db['wins'] + db['losses']
                wr = (db['wins'] / total * 100) if total > 0 else 0
                
                payload = {
                    "content": (f"✨ **{signal.upper()}**\n"
                                f"🪙 **${symbol.split('/')[0]}**\n"
                                f"💵 Entry: {entry}\n"
                                f"🛑 SL: {t_data['sl']:.4f}\n"
                                f"🎯 TP1: {t_data['tp1']:.4f}\n"
                                f"🎯 TP2: {t_data['tp2']:.4f}\n"
                                f"🎯 TP3: {t_data['tp3']:.4f}\n\n"
                                f"📊 **Winrate: {wr:.1f}%** ({db['wins']}W | {db['losses']}L)")
                }
                requests.post(DISCORD_WEBHOOK, json=payload)
                log(f"✨ Trade sent for {symbol}")
        except: continue
    
    save_db(db)

if __name__ == "__main__":
    main()
