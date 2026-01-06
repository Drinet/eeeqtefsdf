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
BASE_CAPITAL = 1000.0  # The floor for calculations
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
                if "bias_1w" not in db: db["bias_1w"] = "BULLISH"
                if "bias_3d" not in db: db["bias_3d"] = "BULLISH"
                if "balance" not in db: db["balance"] = 1000.0
                if "first_run_charts" not in db: db["first_run_charts"] = 0
                return db
        except: pass
    return {"wins": 0, "losses": 0, "balance": 1000.0, "bias_1w": "BULLISH", "bias_3d": "BULLISH", "active_trades": {}, "first_run_charts": 0}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

def send_discord_image(content, df, symbol, timeframe):
    """Generates a chart image and posts it to Discord."""
    # Prepare DataFrame for mplfinance
    df_plot = df.copy()
    df_plot['date_idx'] = pd.to_datetime(df_plot['date'], unit='ms')
    df_plot.set_index('date_idx', inplace=True)
    
    # Create SMA for plot
    df_plot['sma_plot'] = ta.sma(df_plot['close'], length=200)
    
    # Save chart to a byte buffer
    buf = io.BytesIO()
    ap = mpf.make_addplot(df_plot['sma_plot'].tail(100), color='orange', width=1.5)
    mpf.plot(df_plot.tail(100), type='candle', style='charles', addplot=ap, 
             savefig=dict(fname=buf, format='png'), title=f"{symbol} ({timeframe}) 200 SMA", volume=False)
    buf.seek(0)

    # Post to Discord
    payload = {"content": content}
    files = {
        "payload_json": (None, json.dumps(payload)),
        "file": (f"{symbol}.png", buf, "image/png")
    }
    requests.post(DISCORD_WEBHOOK, files=files)

def get_ohlcv(symbol, timeframe):
    for name, ex in EXCHANGES.items():
        for p in [f"{symbol}/USDT", f"{symbol}/USD"]:
            try:
                bars = ex.fetch_ohlcv(p, timeframe=timeframe, limit=250)
                if bars and len(bars) >= 200:
                    return bars, ex.fetch_ticker(p)['last'], p, name
            except: continue
    return None, None, None, None

def detect_signal(df, bias, coin, tf_label):
    df['sma200'] = ta.sma(df['close'], length=200)
    curr_p = df['close'].iloc[-1]
    prev_p = df['close'].iloc[-2]
    sma = df['sma200'].iloc[-1]
    prev_sma = df['sma200'].iloc[-2]

    print(f"[{tf_label}] {coin:5} | Price: {curr_p:10.2f} | SMA: {sma:10.2f}")

    if bias == "BULLISH" and prev_p > prev_sma and curr_p <= sma:
        return "Long trade"
    if bias == "BEARISH" and prev_p < prev_sma and curr_p >= sma:
        return "Short trade"
    return None

def update_trades(db):
    active = db['active_trades']
    changed = False
    for trade_id in list(active.keys()):
        try:
            t = active[trade_id]
            coin = t['symbol'].split('/')[0]
            _, curr_p, _, _ = get_ohlcv(coin, t['timeframe'])
            if not curr_p: continue

            is_long = (t['side'] == "Long trade")

            if (is_long and curr_p <= t['sl']) or (not is_long and curr_p >= t['sl']):
                if not t['tp1_hit']:
                    db['losses'] += 1
                    db['balance'] -= t['risk_amount']
                    requests.post(DISCORD_WEBHOOK, json={"content": f"💀 **{t['symbol']} ({t['timeframe']}) SL HIT.** Loss recorded."})
                else:
                    requests.post(DISCORD_WEBHOOK, json={"content": f"✋ **{t['symbol']} ({t['timeframe']}) Hit Entry.** Risk-free exit."})
                del active[trade_id]; changed = True; continue

            if not t['tp1_hit'] and ((is_long and curr_p >= t['tp1']) or (not is_long and curr_p <= t['tp1'])):
                db['wins'] += 1
                t['tp1_hit'] = True
                t['sl'] = t['entry']
                db['balance'] += (t['position_usd'] * 0.20 * 0.17)
                requests.post(DISCORD_WEBHOOK, json={"content": f"✅ **{t['symbol']} ({t['timeframe']}) TP1!** SL to Entry."})
                changed = True

            if (is_long and curr_p >= t['tp3']) or (not is_long and curr_p <= t['tp3']):
                requests.post(DISCORD_WEBHOOK, json={"content": f"🚀 **{t['symbol']} ({t['timeframe']}) FULL TP REACHED!**"})
                del active[trade_id]; changed = True
        except: continue
    return changed

def main():
    db = load_db()
    current_bal = db['balance']
    calc_basis = current_bal if current_bal >= 2000 else BASE_CAPITAL
    
    print(f"--- SCAN START | Balance: ${current_bal:.2f} | Basis: ${calc_basis} ---")
    update_trades(db)

    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        coins = requests.get(url, params={'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 250}).json()
        symbols = [c['symbol'].upper() for c in coins if c['symbol'].upper() not in BLACKLIST]
    except: return

    scanned_tokens = 0
    for coin in symbols[:200]:
        for tf, bias, pct in [('1w', db['bias_1w'], 0.06), ('3d', db['bias_3d'], 0.02)]:
            trade_id = f"{coin}_{tf}"
            if trade_id in db['active_trades']: continue

            bars, last_p, pair, _ = get_ohlcv(coin, tf)
            if not bars: continue

            df = pd.DataFrame(bars, columns=['date','open','high','low','close','vol'])
            sig = detect_signal(df, bias, coin, tf)

            # FORCE POST FIRST 2 SCANNED TOKENS AS IMAGES (Regardless of Signal)
            if db['first_run_charts'] < 2:
                status_msg = f"🔎 **INITIAL SCAN PREVIEW**\n🪙 **${coin}** ({tf})\nPrice: {last_p:.4f}\nStatus: Monitoring for 200 SMA touches..."
                send_discord_image(status_msg, df, coin, tf)
                db['first_run_charts'] += 1
                save_db(db)

            if sig:
                pos_size = calc_basis * pct
                is_long = (sig == "Long trade")
                
                if is_long:
                    tp1, tp2, tp3, sl = last_p*1.015, last_p*1.03, last_p*1.05, last_p*0.98
                else:
                    tp1, tp2, tp3, sl = last_p*0.985, last_p*0.97, last_p*0.95, last_p*1.02

                db['active_trades'][trade_id] = {
                    "symbol": pair, "timeframe": tf, "side": sig, "entry": last_p,
                    "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp1_hit": False,
                    "position_usd": pos_size, "risk_amount": pos_size * 0.02
                }
                
                total = db['wins'] + db['losses']
                wr = (db['wins'] / total * 100) if total > 0 else 0
                
                msg = (f"🔥 **{sig.upper()} ({tf})**\n🪙 **${coin}**\nEntry: {last_p:.4f}\n"
                       f"🎯 TP1: {tp1:.4f} | TP3: {tp3:.4f}\n🛡️ SL: {sl:.4f}\n"
                       f"💰 Size: ${pos_size:.2f}\n📊 Winrate: {wr:.1f}% ({db['wins']}W | {db['losses']}L)")
                
                # Post trade with Chart
                send_discord_image(msg, df, coin, tf)
        
        time.sleep(0.05)

    save_db(db)
    print("--- SCAN FINISHED ---")

if __name__ == "__main__":
    main()
