import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import json

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
DB_FILE = "trade_history.json"
STARTING_BALANCE = 1000.0
TIMEFRAME = '1w'  # Weekly for 200 SMA

EXCHANGES = {
    "binance": ccxt.binance({'enableRateLimit': True}),
    "kraken": ccxt.kraken({'enableRateLimit': True}),
    "gateio": ccxt.gateio({'enableRateLimit': True})
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {"wins": 0, "losses": 0, "balance": STARTING_BALANCE, "bias": "BULLISH", "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_ohlcv(symbol):
    for name, ex in EXCHANGES.items():
        for p in [f"{symbol}/USDT", f"{symbol}/USD"]:
            try:
                # Need at least 200 bars for SMA
                bars = ex.fetch_ohlcv(p, timeframe=TIMEFRAME, limit=250)
                if bars: return bars, ex.fetch_ticker(p)['last'], p
            except: continue
    return None, None, None

def detect_signal(df, bias):
    if len(df) < 200: return None
    df['sma200'] = ta.sma(df['close'], length=200)
    
    curr_p = df['close'].iloc[-1]
    prev_p = df['close'].iloc[-2] # Previous week close
    sma = df['sma200'].iloc[-1]
    prev_sma = df['sma200'].iloc[-2]

    # LONG: Bias Bullish + Hits SMA from top (Support)
    if bias == "BULLISH":
        if prev_p > prev_sma and curr_p <= sma:
            return "Long trade"

    # SHORT: Bias Bearish + Hits SMA from bottom (Resistance)
    elif bias == "BEARISH":
        if prev_p < prev_sma and curr_p >= sma:
            return "Short trade"
            
    return None

def update_trades(db):
    active = db['active_trades']
    changed = False
    for sym in list(active.keys()):
        try:
            t = active[sym]
            # Use a faster timeframe check for current price to track TPs/SL accurately
            _, curr_p, _ = get_ohlcv(sym.split('/')[0])
            is_long = (t['side'] == "Long trade")

            # SL Check (2% drop/rise)
            if (is_long and curr_p <= t['sl']) or (not is_long and curr_p >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= (t['position_usd'] * 0.02)
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL HIT.**"})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{sym} Closed at Entry (Risk-Free).**"})
                del active[sym]; changed = True; continue

            # TP1 HIT (20% out + SL to Entry)
            if not t['tp1_hit'] and ((is_long and curr_p >= t['tp1']) or (not is_long and curr_p <= t['tp1'])):
                db['wins'] += 1 
                t['tp1_hit'] = True
                t['sl'] = t['entry'] 
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 REACHED!** 20% position closed. SL moved to entry."})
                changed = True

            # TP2 HIT (50% out)
            if not t.get('tp2_hit', False) and ((is_long and curr_p >= t['tp2']) or (not is_long and curr_p <= t['tp2'])):
                t['tp2_hit'] = True
                requests.post(DISCORD_WEBHOOK, json={"content": f"🎯 **{sym} TP2 REACHED!** 50% position closed."})
                changed = True

            # TP3 HIT (Final 30% out)
            if (is_long and curr_p >= t['tp3']) or (not is_long and curr_p <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 MOONED!** Trade fully closed."})
                del active[sym]; changed = True
        except: continue
    return changed

def main():
    db = load_db()
    update_trades(db)
    
    bias = db.get("bias", "BULLISH")
    
    # Top 50 coins to reduce API load every 15 mins
    url = "https://api.coingecko.com/api/v3/coins/markets"
    try:
        coins_data = requests.get(url, params={'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 50}).json()
        symbols = [c['symbol'].upper() for c in coins_data if c['symbol'].lower() not in ['usdt', 'usdc', 'dai']]
    except: return

    for coin in symbols:
        if any(coin in k for k in db['active_trades']): continue
        bars, last_p, pair = get_ohlcv(coin)
        if not bars: continue
        
        df = pd.DataFrame(bars, columns=['date','open','high','low','close','vol'])
        sig = detect_signal(df, bias)
        
        if sig:
            is_long = (sig == "Long trade")
            if is_long:
                tp1, tp2, tp3 = last_p * 1.1706, last_p * 1.5873, last_p * 2.5794
                sl = last_p * 0.98
            else:
                tp1, tp2, tp3 = last_p * 0.8294, last_p * 0.65, last_p * 0.50
                sl = last_p * 1.02

            db['active_trades'][pair] = {
                "side": sig, "entry": last_p, "sl": sl,
                "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "tp1_hit": False, "tp2_hit": False, "position_usd": 100.0
            }
            
            total = db['wins'] + db['losses']
            wr = (db['wins'] / total * 100) if total > 0 else 0
            
            msg = (f"🔥 **{sig.upper()}**\n🪙 **${coin}**\nEntry: {last_p:.4f}\n"
                   f"🎯 TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f}\n"
                   f"🛡️ SL: {sl:.4f}\n\n📊 Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)")
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
    
    save_db(db)

if __name__ == "__main__":
    main()
