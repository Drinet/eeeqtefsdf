import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import json
import time
import mplfinance as mpf
import io

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
DB_FILE = "trade_history.json"
BASE_CAPITAL = 1000.0
BLACKLIST = ['STETH', 'WBTC', 'USDG', 'TBTC', 'TUSD', 'NFT', 'USDT', 'USDC', 'DAI', 'FDUSD', 'WETH']

EXCHANGES = {
    "binance": ccxt.binance({'enableRateLimit': True}),
    "bybit": ccxt.bybit({'enableRateLimit': True})
}

# --- STYLING (Nice Black Theme) ---
DARK_STYLE = mpf.make_mpf_style(
    base_mpf_style='binance', 
    facecolor='#000000',     # Pure Black Background
    gridcolor='#1A1A1A',     # Very Subtle Grid
    figcolor='#000000',      # Figure border black
    y_on_right=False,
    marketcolors=mpf.make_marketcolors(up='#00ff88', down='#ff3355', inherit=True)
)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                db = json.load(f)
                for k, d in [("wins", 0), ("losses", 0), ("balance", 1000.0), 
                             ("bias_1w", "BULLISH"), ("bias_3d", "BULLISH"), ("active_trades", {})]:
                    if k not in db: db[k] = d
                return db
        except: pass
    return {"wins": 0, "losses": 0, "balance": 1000.0, "bias_1w": "BULLISH", "bias_3d": "BULLISH", "active_trades": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def send_discord_with_chart(content, df, coin, timeframe):
    """Generates the dark-themed chart and uploads to Discord Webhook."""
    df_plot = df.tail(100).copy()
    df_plot['date_idx'] = pd.to_datetime(df_plot['date'], unit='ms')
    df_plot.set_index('date_idx', inplace=True)
    
    buf = io.BytesIO()
    ap = [mpf.make_addplot(df_plot['sma200'], color='#00d9ff', width=1.8)] # Cyan SMA
    
    mpf.plot(df_plot, type='candle', style=DARK_STYLE, 
             addplot=ap, figsize=(11, 6), 
             savefig=dict(fname=buf, format='png', bbox_inches='tight'), 
             title=f"\n{coin} {timeframe} ANALYSIS", volume=False)
    buf.seek(0)

    payload = {"content": content}
    files = {
        "payload_json": (None, json.dumps(payload)),
        "files[0]": (f"{coin}_chart.png", buf, "image/png")
    }
    requests.post(DISCORD_WEBHOOK, files=files)

def get_ohlcv(symbol, timeframe):
    for name, ex in EXCHANGES.items():
        try:
            pair = f"{symbol}/USDT"
            bars = ex.fetch_ohlcv(pair, timeframe=timeframe, limit=250)
            if bars and len(bars) >= 200:
                ticker = ex.fetch_ticker(pair)
                return bars, ticker['last'], pair
        except: continue
    return None, None, None

def update_trades(db):
    active = db['active_trades']
    changed = False
    for trade_id in list(active.keys()):
        try:
            t = active[trade_id]
            coin = t['symbol'].split('/')[0]
            _, curr_p, _ = get_ohlcv(coin, t['timeframe'])
            if not curr_p: continue

            is_long = (t['side'] == "Long trade")

            # Check Stop Loss
            if (is_long and curr_p <= t['sl']) or (not is_long and curr_p >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= (t['position_usd'] * 0.02)
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{t['symbol']} SL HIT.** Total Losses: {db['losses']}"})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{t['symbol']} Entry Exit.** Moved to entry after TP1."})
                del active[trade_id]; changed = True; continue

            # Check TP1
            if not t['tp1_hit'] and ((is_long and curr_p >= t['tp1']) or (not is_long and curr_p <= t['tp1'])):
                db['wins'] += 1
                t['tp1_hit'] = True
                t['sl'] = t['entry'] # Move SL to entry
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{t['symbol']} TP1 HIT!** SL moved to Entry."})
                changed = True
        except: continue
    return changed

def main():
    db = load_db()
    calc_basis = db['balance'] if db['balance'] >= 2000 else BASE_CAPITAL
    update_trades(db)

    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        coins = requests.get(url, params={'vs_currency': 'usd', 'per_page': 100}).json()
        symbols = [c['symbol'].upper() for c in coins if c['symbol'].upper() not in BLACKLIST]
    except: return

    previews_sent = 0
    for coin in symbols:
        for tf, bias, pct in [('1w', db['bias_1w'], 0.06), ('3d', db['bias_3d'], 0.02)]:
            trade_id = f"{coin}_{tf}"
            if trade_id in db['active_trades']: continue

            bars, last_p, pair = get_ohlcv(coin, tf)
            if not bars: continue

            df = pd.DataFrame(bars, columns=['date','open','high','low','close','vol'])
            df['sma200'] = ta.sma(df['close'], length=200)

            # --- MANDATORY FIRST 2 CHARTS ---
            if previews_sent < 2:
                preview_msg = f"🔍 **INITIAL SCAN**\nToken: **${coin}**\nTimeframe: {tf}\nStatus: System Online 🟢"
                send_discord_with_chart(preview_msg, df, coin, tf)
                previews_sent += 1

            # --- SIGNAL DETECTION ---
            curr_p, prev_p = df['close'].iloc[-1], df['close'].iloc[-2]
            sma, prev_sma = df['sma200'].iloc[-1], df['sma200'].iloc[-2]

            sig = None
            if bias == "BULLISH" and prev_p > prev_sma and curr_p <= sma: sig = "Long trade"
            elif bias == "BEARISH" and prev_p < prev_sma and curr_p >= sma: sig = "Short trade"

            if sig:
                # 2% SL, 1.5% TP1, 3% TP2, 5% TP3
                if sig == "Long trade":
                    sl, tp1, tp2, tp3 = last_p*0.98, last_p*1.015, last_p*1.03, last_p*1.05
                else:
                    sl, tp1, tp2, tp3 = last_p*1.02, last_p*0.985, last_p*0.97, last_p*0.95

                total = db['wins'] + db['losses']
                wr = (db['wins'] / total * 100) if total > 0 else 0
                
                msg = (f"🔥 **{sig.upper()}**\n🪙 **${coin}**\n"
                       f"Entry: {last_p:.4f}\n🎯 TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f}\n"
                       f"🛡️ SL: {sl:.4f}\n📈 Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)")
                
                send_discord_with_chart(msg, df, coin, tf)
                db['active_trades'][trade_id] = {
                    "symbol": pair, "timeframe": tf, "side": sig, "entry": last_p,
                    "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp1_hit": False,
                    "position_usd": calc_basis * pct
                }

    save_db(db)

if __name__ == "__main__":
    main()
