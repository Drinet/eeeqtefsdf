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
STARTING_BALANCE = 500.0  # Resetting balance as requested
TIMEFRAME = '4h'          # Using 4-hour timeframe for reliability

# --- RISK SETTINGS ---
LEVERAGE = 10
SL_PERCENT = 0.02         # 2% SL
TP1_PERCENT = 0.015       # 1.5% TP
TP2_PERCENT = 0.03        # 3.0% TP
TP3_PERCENT = 0.05        # 5.0% TP
DOLLAR_RISK_PER_TRADE = 10.0  # Amount to lose if SL hit
POSITION_SIZE_USD = DOLLAR_RISK_PER_TRADE / SL_PERCENT  # ~$500 position size
MARGIN_REQUIRED = POSITION_SIZE_USD / LEVERAGE

EXCHANGES = {
    "kraken": ccxt.kraken({'enableRateLimit': True}),
    "binance": ccxt.binance({'enableRateLimit': True}),
    "gateio": ccxt.gateio({'enableRateLimit': True})
}

def log(msg):
    print(f"DEBUG: {msg}", flush=True)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                db = json.load(f)
                # Ensure structure exists
                for k in ["wins", "losses", "balance", "active_trades"]:
                    if k not in db: db[k] = 0 if "wins" in k or "losses" in k else (STARTING_BALANCE if "balance" in k else {})
                return db
        except: pass
    return {"wins": 0, "losses": 0, "balance": STARTING_BALANCE, "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_top_coins():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 120, 'page': 1}
        data = requests.get(url, params=params).json()
        excluded = ['usdt', 'usdc', 'dai', 'fdusd', 'pyusd', 'usde', 'steth', 'wbtc', 'weth']
        return [c['symbol'].upper() for c in data if c['symbol'].lower() not in excluded]
    except: return []

def get_ohlcv(symbol):
    for name, ex in EXCHANGES.items():
        for p in [f"{symbol}/USDT", f"{symbol}/USD"]:
            try:
                bars = ex.fetch_ohlcv(p, timeframe=TIMEFRAME, limit=300)
                if bars: return bars, ex.fetch_ticker(p)['last'], p, name
            except: continue
    return None, None, None, None

def detect_signal(df):
    """EMA 200 + MACD + Bollinger Band Confluence"""
    if len(df) < 200: return None
    
    # Indicators
    df['ema200'] = ta.ema(df['close'], length=200)
    macd = ta.macd(df['close'])
    df['macd'] = macd['MACD_12_26_9']
    df['signal'] = macd['MACDs_12_26_9']
    bbands = ta.bbands(df['close'], length=20, std=2)
    df['bb_mid'] = bbands['BBM_20_2.0']

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Bullish: Price > EMA200 AND MACD Cross Up AND Price > BB Mid
    if curr['close'] > curr['ema200']:
        if prev['macd'] < prev['signal'] and curr['macd'] > curr['signal']:
            if curr['close'] > curr['bb_mid']:
                return "Long trade"

    # Bearish: Price < EMA200 AND MACD Cross Down AND Price < BB Mid
    if curr['close'] < curr['ema200']:
        if prev['macd'] > prev['signal'] and curr['macd'] < curr['signal']:
            if curr['close'] < curr['bb_mid']:
                return "Short trade"
                
    return None

def update_active_trades(db):
    active = db['active_trades']
    changed = False
    for sym in list(active.keys()):
        try:
            t = active[sym]
            _, curr_price, _, _ = get_ohlcv(sym.split('/')[0])
            is_long = (t['side'] == "Long trade")
            
            # TP3 Hit (Close)
            if (is_long and curr_price >= t['tp3']) or (not is_long and curr_price <= t['tp3']):
                db['balance'] += (POSITION_SIZE_USD * 0.05)
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 MOONED!** Final take profit hit."})
                del active[sym]
                changed = True; continue

            # TP2 Hit
            if not t['tp2_hit'] and ((is_long and curr_price >= t['tp2']) or (not is_long and curr_price <= t['tp2'])):
                db['balance'] += (POSITION_SIZE_USD * 0.03)
                t['tp2_hit'] = True
                requests.post(DISCORD_WEBHOOK, json={"content": f"🎯 **{sym} TP2 Secure.** Trend continuing."})

            # TP1 Hit (Move SL to entry)
            if not t['tp1_hit'] and ((is_long and curr_price >= t['tp1']) or (not is_long and curr_price <= t['tp1'])):
                db['balance'] += (POSITION_SIZE_USD * 0.015)
                db['wins'] += 1
                t['tp1_hit'] = True
                t['sl'] = t['entry']  # Move SL to Entry
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 Hit.** Win counted, SL moved to entry."})

            # SL Hit
            if (is_long and curr_price <= t['sl']) or (not is_long and curr_price >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= DOLLAR_RISK_PER_TRADE
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL Hit.** Trade lost."})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{sym} hit Entry SL.** Risk-free exit."})
                del active[sym]
                changed = True
        except: continue
    return changed

def main():
    db = load_db()
    update_active_trades(db)
    
    coins = get_top_coins()
    for coin in coins:
        if any(coin in k for k in db['active_trades']): continue
        
        bars, last_price, pair, ex_name = get_ohlcv(coin)
        if not bars: continue
        
        df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        signal = detect_signal(df)
        
        if signal:
            entry = last_price
            m = 1 if signal == "Long trade" else -1
            t_data = {
                "side": signal, "entry": entry, "sl": entry * (1 - (SL_PERCENT * m)),
                "tp1": entry * (1 + (TP1_PERCENT * m)), "tp2": entry * (1 + (TP2_PERCENT * m)),
                "tp3": entry * (1 + (TP3_PERCENT * m)), "tp1_hit": False, "tp2_hit": False
            }
            db['active_trades'][pair] = t_data
            
            total = db['wins'] + db['losses']
            wr = (db['wins'] / total * 100) if total > 0 else 0
            msg = (f"⚡ **{signal.upper()}**\n🪙 **${coin}**\n"
                   f"Entry: {entry:.4f}\n"
                   f"TP1: {t_data['tp1']:.4f} | TP2: {t_data['tp2']:.4f} | TP3: {t_data['tp3']:.4f}\n"
                   f"SL: {t_data['sl']:.4f}\n"
                   f"Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)")
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
    
    save_db(db)

if __name__ == "__main__":
    main()
