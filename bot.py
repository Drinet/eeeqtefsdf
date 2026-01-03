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
STARTING_BALANCE = 500.0
TIMEFRAME = '4h'

# --- RISK SETTINGS ---
LEVERAGE = 10
SL_PERCENT = 0.02         # 2% SL
TP1_PERCENT = 0.015       # 1.5% TP (Win + SL to Entry)
TP2_PERCENT = 0.03        # 3.0% TP
TP3_PERCENT = 0.05        # 5.0% TP
DOLLAR_RISK_PER_TRADE = 10.0
POSITION_SIZE_USD = DOLLAR_RISK_PER_TRADE / SL_PERCENT  # Total Trade Value
MARGIN_REQUIRED = POSITION_SIZE_USD / LEVERAGE

EXCHANGES = {
    "kraken": ccxt.kraken({'enableRateLimit': True}),
    "binance": ccxt.binance({'enableRateLimit': True}),
    "gateio": ccxt.gateio({'enableRateLimit': True})
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                db = json.load(f)
                return db
        except: pass
    return {"wins": 0, "losses": 0, "balance": STARTING_BALANCE, "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def get_ohlcv(symbol):
    for name, ex in EXCHANGES.items():
        for p in [f"{symbol}/USDT", f"{symbol}/USD"]:
            try:
                bars = ex.fetch_ohlcv(p, timeframe=TIMEFRAME, limit=250)
                if bars: return bars, ex.fetch_ticker(p)['last'], p, name
            except: continue
    return None, None, None, None

def detect_signal(df):
    if len(df) < 200: return None
    df['ema200'] = ta.ema(df['close'], length=200)
    macd = ta.macd(df['close'])
    df['m_line'] = macd.iloc[:, 0]
    df['m_sig'] = macd.iloc[:, 2]
    bbands = ta.bbands(df['close'], length=20, std=2)
    df['bb_mid'] = bbands.iloc[:, 1] 

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Bullish Confluence
    if curr['close'] > curr['ema200']:
        if prev['m_line'] < prev['m_sig'] and curr['m_line'] > curr['m_sig']:
            if curr['close'] > curr['bb_mid']:
                return "Long trade"

    # Bearish Confluence
    if curr['close'] < curr['ema200']:
        if prev['m_line'] > prev['m_sig'] and curr['m_line'] < prev['m_sig']:
            if curr['close'] < curr['bb_mid']:
                return "Short trade"
    return None

def update_trades(db):
    active = db['active_trades']
    changed = False
    for sym in list(active.keys()):
        try:
            t = active[sym]
            _, curr_p, _, _ = get_ohlcv(sym.split('/')[0])
            is_long = (t['side'] == "Long trade")

            # SL Check
            if (is_long and curr_p <= t['sl']) or (not is_long and curr_p >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= DOLLAR_RISK_PER_TRADE
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{sym} SL HIT.** Balance: ${db['balance']:.2f}"})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{sym} Closed at Entry.** Risk-free exit."})
                del active[sym]; changed = True; continue

            # TP Checks
            if not t['tp1_hit'] and ((is_long and curr_p >= t['tp1']) or (not is_long and curr_p <= t['tp1'])):
                db['wins'] += 1
                db['balance'] += (POSITION_SIZE_USD * 0.015)
                t['tp1_hit'] = True
                t['sl'] = t['entry'] 
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{sym} TP1 HIT!** Winrate: {(db['wins']/(db['wins']+db['losses'])*100):.1f}%"})

            if not t.get('tp2_hit', False) and ((is_long and curr_p >= t['tp2']) or (not is_long and curr_p <= t['tp2'])):
                db['balance'] += (POSITION_SIZE_USD * 0.03)
                t['tp2_hit'] = True
                requests.post(DISCORD_WEBHOOK, json={"content": f"🎯 **{sym} TP2 SECURED.** Balance: ${db['balance']:.2f}"})

            if (is_long and curr_p >= t['tp3']) or (not is_long and curr_p <= t['tp3']):
                db['balance'] += (POSITION_SIZE_USD * 0.05)
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{sym} TP3 MOONED!** Final Profit Taken."})
                del active[sym]; changed = True
        except: continue
    return changed

def main():
    db = load_db()
    update_trades(db)
    
    url = "https://api.coingecko.com/api/v3/coins/markets"
    coins_data = requests.get(url, params={'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 100}).json()
    symbols = [c['symbol'].upper() for c in coins_data if c['symbol'].lower() not in ['usdt', 'usdc', 'dai']]

    for coin in symbols:
        if any(coin in k for k in db['active_trades']): continue
        bars, last_p, pair, ex_name = get_ohlcv(coin)
        if not bars: continue
        
        df = pd.DataFrame(bars, columns=['date','open','high','low','close','vol'])
        sig = detect_signal(df)
        
        if sig:
            m = 1 if sig == "Long trade" else -1
            t_data = {
                "side": sig, "entry": last_p, "sl": last_p * (1 - (SL_PERCENT * m)),
                "tp1": last_p * (1 + (TP1_PERCENT * m)), "tp2": last_p * (1 + (TP2_PERCENT * m)),
                "tp3": last_p * (1 + (TP3_PERCENT * m)), "tp1_hit": False, "tp2_hit": False
            }
            db['active_trades'][pair] = t_data
            total = db['wins'] + db['losses']
            wr = (db['wins'] / total * 100) if total > 0 else 0
            
            msg = (f"📈 **{sig.upper()}**\n🪙 **${coin}**\nEntry: {last_p:.4f}\n"
                   f"TP1: {t_data['tp1']:.4f} | TP2: {t_data['tp2']:.4f} | TP3: {t_data['tp3']:.4f}\n"
                   f"SL: {t_data['sl']:.4f}\n📊 Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)")
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
    save_db(db)

if __name__ == "__main__":
    main()
