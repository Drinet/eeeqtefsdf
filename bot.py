import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
import sys
from scipy.signal import argrelextrema

# --- CONFIG & DATABASE ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

# --- TRADE SETTINGS ---
SL_PCT = 0.02    # 2%
TP1_PCT = 0.015  # 1.5%
TP2_PCT = 0.03   # 3%
TP3_PCT = 0.05   # 5%

def log(msg):
    print(msg, flush=True)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"wins": 0, "losses": 0, "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

def get_top_coins():
    """Fetches top coins and removes specific exclusions."""
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
    except: return []

def detect_triple_divergence(df, order=4):
    """Detects 3 consecutive diverging pivots using candle bodies (closes)."""
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 100: return None

    # BULLISH: Price Closes Lower Lows | RSI Higher Lows
    low_pivots = argrelextrema(df.close.values, np.less, order=order)[0]
    if len(low_pivots) >= 3:
        p1, p2, p3 = df.close.iloc[low_pivots[-3:]].values
        r1, r2, r3 = df.RSI.iloc[low_pivots[-3:]].values
        if p1 > p2 > p3 and r1 < r2 < r3:
            return "Long trade"

    # BEARISH: Price Closes Higher Highs | RSI Lower Highs
    high_pivots = argrelextrema(df.close.values, np.greater, order=order)[0]
    if len(high_pivots) >= 3:
        p1, p2, p3 = df.close.iloc[high_pivots[-3:]].values
        r1, r2, r3 = df.RSI.iloc[high_pivots[-3:]].values
        if p1 < p2 < p3 and r1 > r2 > r3:
            return "Short trade"
    return None

def update_tracker(db):
    active = db['active_trades']
    for sym in list(active.keys()):
        t = active[sym]
        try:
            curr_price = EXCHANGE.fetch_ticker(sym)['last']
            is_long = t['side'] == "Long trade"

            # Check SL
            if (is_long and curr_price <= t['sl']) or (not is_long and curr_price >= t['sl']):
                db['losses'] += 1
                requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **SL HIT: {sym}**\nPrice: {curr_price}"})
                del active[sym]
                continue

            # Check TP1 (Win Counted, SL to Entry)
            if not t['tp1_hit'] and ((is_long and curr_price >= t['tp1']) or (not is_long and curr_price <= t['tp1'])):
                t['tp1_hit'] = True
                t['sl'] = t['entry']
                db['wins'] += 1
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **TP1 HIT: {sym}**\nSL moved to Entry. WIN secured."})

            # Check TP3 (Complete)
            if (is_long and curr_price >= t['tp3']) or (not is_long and curr_price <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"💰 **TP3 MAX PROFIT: {sym}**"})
                del active[sym]
        except Exception as e:
            log(f"Tracker error for {sym}: {e}")

def main():
    if not DISCORD_WEBHOOK:
        log("Error: Set DISCORD_WEBHOOK_URL in Secrets!")
        return

    db = load_db()
    update_tracker(db)
    symbols = get_top_coins()
    total = len(symbols)
    log(f"Starting scan for {total} filtered coins...")

    for i, symbol in enumerate(symbols, 1):
        if i % 10 == 0: log(f"[{i}/{total}] Scanning...")
        if symbol in db['active_trades']: continue
        
        try:
            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=200)
            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
            signal = detect_triple_divergence(df, order=4)
            
            if signal:
                log(f"✨ {signal} found for {symbol}")
                entry = float(df['close'].iloc[-1])
                mult = 1 if signal == "Long trade" else -1
                
                t_data = {
                    "side": signal, "entry": entry, "tp1_hit": False,
                    "sl": entry * (1 - SL_PCT * mult),
                    "tp1": entry * (1 + TP1_PCT * mult),
                    "tp2": entry * (1 + TP2_PCT * mult),
                    "tp3": entry * (1 + TP3_PCT * mult)
                }
                db['active_trades'][symbol] = t_data

                total_tr = db['wins'] + db['losses']
                wr = (db['wins'] / total_tr * 100) if total_tr > 0 else 0
                
                msg = (f"🔱 **{signal.upper()}**\n"
                       f"🪙 **${symbol.split('/')[0]}**\n"
                       f"💵 Entry: {entry:,.4f}\n"
                       f"🛑 SL: {t_data['sl']:,.4f}\n"
                       f"🎯 TP1: {t_data['tp1']:,.4f}\n"
                       f"🎯 TP2: {t_data['tp2']:,.4f}\n"
                       f"🎯 TP3: {t_data['tp3']:,.4f}\n\n"
                       f"📈 **Winrate: {wr:.1f}%** ({db['wins']}W | {db['losses']}L)")
                requests.post(DISCORD_WEBHOOK, json={"content": msg})
        except: continue
    
    save_db(db)

if __name__ == "__main__":
    main()
