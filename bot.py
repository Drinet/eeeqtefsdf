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
                if "wins" not in db: db["wins"] = 0
                if "losses" not in db: db["losses"] = 0
                if "active_trades" not in db: db["active_trades"] = {}
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
    """
    Detects 3 consecutive pivots using candle CLOSE (Bodies), ignoring Wicks.
    """
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 100: return None

    # Using df['close'] ensures we only look at candle bodies
    # BULLISH (Price Closes Lower Lows | RSI Higher Lows)
    low_pivots = argrelextrema(df['close'].values, np.less, order=order)[0]
    if len(low_pivots) >= 3:
        p1, p2, p3 = df['close'].iloc[low_pivots[-3:]].values
        r1, r2, r3 = df['RSI'].iloc[low_pivots[-3:]].values
        if p1 > p2 > p3 and r1 < r2 < r3:
            return "Long trade"

    # BEARISH (Price Closes Higher Highs | RSI Lower Highs)
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
            curr_price = ticker['last']
            side = t['side']
            
            # SL HIT
            if (side == "Long trade" and curr_price <= t['sl']) or (side == "Short trade" and curr_price >= t['sl']):
                db['losses'] += 1
                requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL Hit** at {curr_price}"})
                del active[sym]
                continue

            # TP1 HIT
            if not t['tp1_hit']:
                if (side == "Long trade" and curr_price >= t['tp1']) or (side == "Short trade" and curr_price <= t['tp1']):
                    t['tp1_hit'] = True
                    t['sl'] = t['entry'] 
                    db['wins'] += 1
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 Hit!** Win counted, SL moved to entry."})

            # TP3 HIT
            if (side == "Long trade" and curr_price >= t['tp3']) or (side == "Short trade" and curr_price <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"💰 **{sym} TP3 Hit!** Max profit reached."})
                del active[sym]
        except: continue

def main():
    if not DISCORD_WEBHOOK:
        log("Error: Set DISCORD_WEBHOOK_URL in Secrets!")
        return

    db = load_db()
    update_trades(db)
    
    symbols = get_top_coins()
    log(f"Starting scan for {len(symbols)} coins...")

    for i, symbol in enumerate(symbols, 1):
        # SKIP if already in a trade for this coin
        if symbol in db['active_trades']:
            log(f"[{i}/{len(symbols)}] Skipping {symbol} (Trade Active)")
            continue

        try:
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=200)
            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
            
            # Detect divergence using only candle bodies
            signal = detect_triple_divergence(df, order=4)
            
            if signal:
                entry = float(df['close'].iloc[-1])
                mult = 1 if signal == "Long trade" else -1
                
                sl = entry * (1 - (0.02 * mult))
                tp1 = entry * (1 + (0.015 * mult))
                tp2 = entry * (1 + (0.03 * mult))
                tp3 = entry * (1 + (0.05 * mult))

                db['active_trades'][symbol] = {
                    "side": signal, "entry": entry, "sl": sl, 
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp1_hit": False
                }

                total = db['wins'] + db['losses']
                wr = (db['wins'] / total * 100) if total > 0 else 0
                
                payload = {
                    "content": (f"✨ **{signal.upper()}**\n"
                                f"🪙 **${symbol.split('/')[0]}**\n"
                                f"💵 Entry: {entry}\n"
                                f"🛑 SL: {sl:.4f}\n"
                                f"🎯 TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f}\n\n"
                                f"📊 **Winrate: {wr:.1f}%** ({db['wins']}W | {db['losses']}L)")
                }
                requests.post(DISCORD_WEBHOOK, json=payload)
                log(f"✨ Signal found for {symbol}")
        except: continue
    
    save_db(db)

if __name__ == "__main__":
    main()
