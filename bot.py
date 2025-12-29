import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import numpy as np
import json
from scipy.signal import argrelextrema
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')
EXCHANGE = ccxt.kraken()
DB_FILE = "trade_history.json"

# --- TRADING SETTINGS ---
STARTING_BALANCE = 250.0
RISK_PER_TRADE = 0.03  # 3%
SL_PCT = 0.015         # 1.5%
TP1_PCT = 0.01         # 1% (Sell 15%)
TP2_PCT = 0.03         # 3% (Sell 50% of remaining)
TP3_PCT = 0.05         # 5% (Close all)

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {"balance": STARTING_BALANCE, "active_trades": {}, "history": []}

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_top_100():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 100, 'page': 1}
        data = requests.get(url, params=params).json()
        stables = ['usdt', 'usdc', 'dai', 'busd', 'fdusd', 'pyusd', 'usde', 'tusd', 'steth', 'wbtc', 'weth']
        return [c['symbol'].upper() + '/USD' for c in data if c['symbol'].lower() not in stables]
    except:
        return []

def detect_divergence(df, order=5):
    """Detects 4 consecutive diverging pivots (Quadruple)."""
    df['RSI'] = ta.rsi(df['close'], length=14)
    df = df.dropna().reset_index(drop=True)
    if len(df) < 80: return None

    # LONG (Quadruple Bullish: 4 Lower Lows Price / 4 Higher Lows RSI)
    low_peaks = argrelextrema(df.low.values, np.less, order=order)[0]
    if len(low_peaks) >= 4:
        p = df.low.iloc[low_peaks[-4:]].values
        r = df.RSI.iloc[low_peaks[-4:]].values
        if p[0] >= p[1] >= p[2] >= p[3] and r[0] < r[1] < r[2] < r[3]:
            return "LONG"

    # SHORT (Quadruple Bearish: 4 Higher Highs Price / 4 Lower Highs RSI)
    high_peaks = argrelextrema(df.high.values, np.greater, order=order)[0]
    if len(high_peaks) >= 4:
        p = df.high.iloc[high_peaks[-4:]].values
        r = df.RSI.iloc[high_peaks[-4:]].values
        if p[0] <= p[1] <= p[2] <= p[3] and r[0] > r[1] > r[2] > r[3]:
            return "SHORT"
    return None

def main():
    db = load_db()
    symbols = get_top_100()
    
    # Calculate win rate
    wins = len([t for t in db['history'] if t['profit'] > 0])
    total = len(db['history'])
    win_rate = (wins / total * 100) if total > 0 else 0

    for symbol in symbols:
        try:
            # Check if we are already in this trade (Cooldown/Anti-Flood)
            if symbol in db['active_trades']:
                continue

            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=200)
            df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            signal = detect_divergence(df)

            if signal:
                entry = df['close'].iloc[-1]
                sl = entry * (1 - SL_PCT) if signal == "LONG" else entry * (1 + SL_PCT)
                tp1 = entry * (1 + TP1_PCT) if signal == "LONG" else entry * (1 - TP1_PCT)
                tp2 = entry * (1 + TP2_PCT) if signal == "LONG" else entry * (1 - TP2_PCT)
                tp3 = entry * (1 + TP3_PCT) if signal == "LONG" else entry * (1 - TP3_PCT)

                # Store active trade
                db['active_trades'][symbol] = {
                    "side": signal, "entry": entry, "sl": sl, 
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp1_hit": False, "tp2_hit": False
                }

                # Discord Message
                msg = {
                    "content": f"# 🔔 NEW {signal} TRADE\n**Asset:** {symbol}\n**Entry:** ${entry:,.4f}\n**Stop Loss:** ${sl:,.4f}\n"
                               f"**TP1 (15%):** ${tp1:,.4f}\n**TP2 (50%):** ${tp2:,.4f}\n**TP3 (100%):** ${tp3:,.4f}\n"
                               f"**Current Balance:** ${db['balance']:.2f} | **Win Rate:** {win_rate:.1f}%"
                }
                requests.post(DISCORD_WEBHOOK, json=msg)

        except Exception as e:
            print(f"Error on {symbol}: {e}")

    save_db(db)

if __name__ == "__main__":
    main()
