 import ccxt

import pandas as pd

import pandas_ta as ta

import requests

import os

import numpy as np

import sys

from scipy.signal import argrelextrema


# --- CONFIG ---

DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK_URL')

EXCHANGE = ccxt.kraken()


def get_top_coins():

    """Fetches top coins and removes specific exclusions."""

    try:

        # Fetching 200 to ensure we have ~100+ left after filtering

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

        return filtered[:120] # Return roughly the top 100-120 tradeable coins

    except:

        return []


def detect_triple_divergence(df, order=4):

    """Detects 3 consecutive diverging pivots using candle bodies (closes)."""

    df['RSI'] = ta.rsi(df['close'], length=14)

    df = df.dropna().reset_index(drop=True)

    if len(df) < 100: return None


    # BULLISH: Price Closes Lower Lows | RSI Higher Lows

    # argrelextrema finds the 'pits' and 'peaks' in the body closes

    low_pivots = argrelextrema(df.close.values, np.less, order=order)[0]

    if len(low_pivots) >= 3:

        p1, p2, p3 = df.close.iloc[low_pivots[-3:]].values

        r1, r2, r3 = df.RSI.iloc[low_pivots[-3:]].values

        # Pattern: Price is dropping (p1 > p2 > p3), RSI is rising (r1 < r2 < r3)

        if p1 > p2 > p3 and r1 < r2 < r3:

            return "🚀 TRIPLE BULLISH DIV (REVERSAL UP)"


    # BEARISH: Price Closes Higher Highs | RSI Lower Highs

    high_pivots = argrelextrema(df.close.values, np.greater, order=order)[0]

    if len(high_pivots) >= 3:

        p1, p2, p3 = df.close.iloc[high_pivots[-3:]].values

        r1, r2, r3 = df.RSI.iloc[high_pivots[-3:]].values

        # Pattern: Price is rising (p1 < p2 < p3), RSI is dropping (r1 > r2 > r3)

        if p1 < p2 < p3 and r1 > r2 > r3:

            return "🔥 TRIPLE BEARISH DIV (REVERSAL DOWN)"

            

    return None


def main():

    if not DISCORD_WEBHOOK:

        print("Error: Set DISCORD_WEBHOOK_URL in Secrets!", flush=True)

        return


    symbols = get_top_coins()

    total = len(symbols)

    print(f"Starting scan for {total} filtered coins...", flush=True)


    for i, symbol in enumerate(symbols, 1):

        # Forced real-time logging for GitHub Actions

        print(f"[{i}/{total}] Checking {symbol}...", flush=True)

        try:

            # 15m timeframe, limit to 200 bars for better pivot window

            bars = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=200)

            df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close', 'vol'])

            

            signal = detect_triple_divergence(df, order=4)

            

            if signal:

                print(f"✨ MATCH FOUND for {symbol}!", flush=True)

                tv_link = f"https://www.tradingview.com/chart/?symbol=KRAKEN:{symbol.replace('/','')}"

                payload = {

                    "content": f"## {signal}\n**Symbol:** {symbol}\n**Timeframe:** 15m\n[🔍 Open Chart]({tv_link})"

                }

                requests.post(DISCORD_WEBHOOK, json=payload)

        except:

            continue


if __name__ == "__main__":

    main() 
