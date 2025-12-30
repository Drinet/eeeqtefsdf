import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
import sys
import time
from scipy.signal import argrelextrema

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
DB_FILE = "trade_history.json"

# Initialize Multiple Exchanges for Scanning
EXCHANGES = {
    "kraken": ccxt.kraken({'enableRateLimit': True}),
    "binance": ccxt.binance({'enableRateLimit': True}),
    "gateio": ccxt.gateio({'enableRateLimit': True})
}

def log(msg):
    # This keeps the "testing stuff" visible in GitHub Action logs
    print(f"DEBUG: {msg}", flush=True)

def format_price(price):
    if price is None: return "0.00"
    if price < 0.0001:
        return f"{price:.10f}".rstrip('0').rstrip('.')
    elif price < 1:
        return f"{price:.6f}"
    else:
        return f"{price:.4f}"

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
    log("Fetching top 120 coins from CoinGecko...")
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 120, 'page': 1}
        data = requests.get(url, params=params).json()
        
        excluded = ['usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth']
        coins = [c['symbol'].upper() for c in data if c['symbol'].lower() not in excluded]
        return coins
    except Exception as e:
        log(f"CoinGecko Error: {e}")
        return []

def get_ohlcv_multi_exchange(coin_symbol):
    """Tries to find the coin on multiple exchanges."""
    pair = f"{coin_symbol}/USDT"
    # Kraken often uses /USD instead of /USDT
    pairs_to_try = [f"{coin_symbol}/USD", f"{coin_symbol}/USDT"]
    
    for ex_name, exchange in EXCHANGES.items():
        for p in pairs_to_try:
            try:
                # log(f"Checking {ex_name} for {p}...")
                bars = exchange.fetch_ohlcv(p, timeframe='15m', limit=150)
                if bars:
                    ticker = exchange.fetch_ticker(p)
                    return bars, ticker['last'], p, ex_name
            except:
                continue
    return None, None, None, None

def detect_triple_divergence(df, order=4):
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 50: return None

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
    log(f"Updating {len(active)} active trades...")
    
    for sym in list(active.keys()):
        try:
            t = active[sym]
            ex_name = t.get('exchange', 'kraken')
            exchange = EXCHANGES.get(ex_name, EXCHANGES['kraken'])
            ticker = exchange.fetch_ticker(sym)
            curr = ticker['last']
            side = t['side']
            is_long = (side == "Long trade")
            
            # Check TP3
            if (is_long and curr >= t['tp3']) or (not is_long and curr <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 HIT!** Trade closed on {ex_name}."})
                del active[sym]
                continue
            
            # Check TP1 (Moves SL)
            if not t.get('tp1_hit', False):
                if (is_long and curr >= t['tp1']) or (not is_long and curr <= t['tp1']):
                    t['tp1_hit'] = True
                    t['sl'] = t['entry'] 
                    db['wins'] += 1
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 HIT!** SL moved to entry. (Win counted)"})

            # Check SL
            if (is_long and curr <= t['sl']) or (not is_long and curr >= t['sl']):
                if not t.get('tp1_hit', False):
                    db['losses'] += 1
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL Hit** on {ex_name}. (Loss counted)"})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"⚠️ **{sym} Closed at Entry** after TP1 hit."})
                del active[sym]
        except Exception as e:
            log(f"Update error for {sym}: {e}")

def main():
    db = load_db()
    update_trades(db)
    
    coins = get_top_coins()
    log(f"Starting Scan for {len(coins)} Coins...")

    for i, coin in enumerate(coins, 1):
        # Prevent duplicate trades
        is_active = any(coin in key for key in db['active_trades'].keys())
        if is_active:
            log(f"[{i}/{len(coins)}] {coin} - SKIP (Already Active)")
            continue

        log(f"[{i}/{len(coins)}] {coin} - Scanning...")
        
        bars, last_price, pair_name, ex_name = get_ohlcv_multi_exchange(coin)
        
        if not bars:
            log(f"      {coin} - Error: Not found on Kraken/Binance/Gateio.")
            continue

        df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        signal = detect_triple_divergence(df)
        
        if signal:
            entry = last_price
            mult = 1 if signal == "Long trade" else -1
            
            t_data = {
                "side": signal, "entry": entry, "exchange": ex_name,
                "sl": entry * (1 - (0.02 * mult)),
                "tp1": entry * (1 + (0.015 * mult)),
                "tp2": entry * (1 + (0.03 * mult)),
                "tp3": entry * (1 + (0.05 * mult)),
                "tp1_hit": False
            }

            db['active_trades'][pair_name] = t_data
            total = db['wins'] + db['losses']
            wr = (db['wins'] / total * 100) if total > 0 else 0
            
            msg = (f"✨ **{signal.upper()}**\n"
                   f"🪙 **${coin}** ({ex_name})\n"
                   f"💵 Entry: {format_price(entry)}\n"
                   f"🛑 SL: {format_price(t_data['sl'])}\n"
                   f"🎯 TP1: {format_price(t_data['tp1'])} | TP2: {format_price(t_data['tp2'])} | TP3: {format_price(t_data['tp3'])}\n\n"
                   f"📊 **Winrate: {wr:.1f}%** ({db['wins']}W | {db['losses']}L)")
            
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
            log(f"!!! SIGNAL FOUND: {coin} on {ex_name} !!!")

    save_db(db)
    log("Scan Complete.")

if __name__ == "__main__":
    main()
