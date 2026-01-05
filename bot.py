import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import json
import time

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
DB_FILE = "trade_history.json"
STARTING_BALANCE = 1000.0
TIMEFRAME = '1w'
POSITION_SIZE_USD = 100.0

# Excluded list as requested
BLACKLIST = ['STETH', 'WBTC', 'USDG', 'TBTC', 'TUSD', 'NFT', 'USDT', 'USDC', 'DAI', 'FDUSD', 'WETH']

EXCHANGES = {
    "binance": ccxt.binance({'enableRateLimit': True}),
    "kraken": ccxt.kraken({'enableRateLimit': True}),
    "gateio": ccxt.gateio({'enableRateLimit': True}),
    "bybit": ccxt.bybit({'enableRateLimit': True})
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                db = json.load(f)
                if "bias" not in db: db["bias"] = "BULLISH"
                return db
        except: pass
    return {"wins": 0, "losses": 0, "balance": STARTING_BALANCE, "bias": "BULLISH", "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_ohlcv(symbol):
    """Checks all 4 exchanges for the coin."""
    for name, ex in EXCHANGES.items():
        for p in [f"{symbol}/USDT", f"{symbol}/USD"]:
            try:
                bars = ex.fetch_ohlcv(p, timeframe=TIMEFRAME, limit=300)
                if bars and len(bars) >= 200:
                    return bars, ex.fetch_ticker(p)['last'], p, name
            except: continue
    return None, None, None, None

def detect_signal(df, bias, coin):
    df['sma200'] = ta.sma(df['close'], length=200)
    curr_p = df['close'].iloc[-1]
    prev_p = df['close'].iloc[-2]
    sma = df['sma200'].iloc[-1]
    prev_sma = df['sma200'].iloc[-2]

    # Enhanced Console Logging for Actions Tab
    print(f"Checking {coin:6} | Price: {curr_p:12.4f} | SMA200: {sma:12.4f} | Bias: {bias}")

    if bias == "BULLISH" and prev_p > prev_sma and curr_p <= sma:
        return "Long trade"
    if bias == "BEARISH" and prev_p < prev_sma and curr_p >= sma:
        return "Short trade"
    return None

def update_trades(db):
    active = db['active_trades']
    changed = False
    for sym in list(active.keys()):
        try:
            t = active[sym]
            coin_name = sym.split('/')[0]
            _, curr_p, _, _ = get_ohlcv(coin_name)
            if not curr_p: continue

            is_long = (t['side'] == "Long trade")

            # SL CHECK
            if (is_long and curr_p <= t['sl']) or (not is_long and curr_p >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= (POSITION_SIZE_USD * 0.02)
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL HIT.** Loss recorded."})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{sym} SL HIT at Entry.** Risk-free exit."})
                del active[sym]; changed = True; continue

            # TP1 (17.06% move | 20% out)
            if not t['tp1_hit'] and ((is_long and curr_p >= t['tp1']) or (not is_long and curr_p <= t['tp1'])):
                db['wins'] += 1
                t['tp1_hit'] = True
                t['sl'] = t['entry']
                db['balance'] += (POSITION_SIZE_USD * 0.20 * 0.1706)
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 HIT!** SL moved to Entry."})
                changed = True

            # TP2 & TP3 logic (Updated for specific long/short goals)
            if not t.get('tp2_hit', False) and ((is_long and curr_p >= t['tp2']) or (not is_long and curr_p <= t['tp2'])):
                t['tp2_hit'] = True
                move_pct = 0.5873 if is_long else 0.35
                db['balance'] += (POSITION_SIZE_USD * 0.50 * move_pct)
                requests.post(DISCORD_WEBHOOK, json={"content": f"🎯 **{sym} TP2 REACHED.** (50% Out)"})
                changed = True

            if (is_long and curr_p >= t['tp3']) or (not is_long and curr_p <= t['tp3']):
                move_pct = 1.5794 if is_long else 0.50
                db['balance'] += (POSITION_SIZE_USD * 0.30 * move_pct)
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 MOONED!** Trade Closed."})
                del active[sym]; changed = True
        except: continue
    return changed

def main():
    db = load_db()
    print(f"--- STARTING SCAN (Top 200) | BIAS: {db['bias']} | BAL: ${db['balance']:.2f} ---")
    update_trades(db)
    
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        # Fetching 250 to ensure we have enough after filtering blacklist
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 250, 'page': 1}
        coins_data = requests.get(url, params=params).json()
        symbols = [c['symbol'].upper() for c in coins_data if c['symbol'].upper() not in BLACKLIST]
    except Exception as e:
        print(f"Coingecko API Error: {e}")
        return

    for coin in symbols[:200]: # Take top 200 after blacklist
        if any(coin in k for k in db['active_trades']): continue
        
        bars, last_p, pair, ex_name = get_ohlcv(coin)
        
        if not bars:
            print(f"Skipping {coin:6} | Reason: No 200w history found on 4 exchanges.")
            continue
        
        df = pd.DataFrame(bars, columns=['date','open','high','low','close','vol'])
        sig = detect_signal(df, db['bias'], coin)
        
        if sig:
            is_long = (sig == "Long trade")
            if is_long:
                tp1, tp2, tp3 = last_p * 1.1706, last_p * 1.5873, last_p * 2.5794
                sl = last_p * 0.98
            else:
                tp1, tp2, tp3 = last_p * 0.8294, last_p * 0.65, last_p * 0.50
                sl = last_p * 1.02

            db['active_trades'][pair] = {
                "side": sig, "entry": last_p, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "tp1_hit": False, "tp2_hit": False, "position_usd": POSITION_SIZE_USD
            }
            
            total = db['wins'] + db['losses']
            wr = (db['wins'] / total * 100) if total > 0 else 0
            
            msg = (f"🔥 **{sig.upper()}**\n🪙 **${coin}**\nEntry: {last_p:.4f}\n"
                   f"🎯 TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f}\n"
                   f"🛡️ SL: {sl:.4f}\n\n📊 Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)\n"
                   f"💰 Balance: ${db['balance']:.2f}")
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
        
        time.sleep(0.05) # Small sleep to respect rate limits

    save_db(db)
    print("--- SCAN FINISHED ---")

if __name__ == "__main__":
    main()
