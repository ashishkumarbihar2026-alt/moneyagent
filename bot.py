import requests
import hashlib
import hmac
import time
import json
from datetime import datetime, timedelta

import os
API_KEY = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
SYMBOL = "BTCINR"
BASE_URL = "https://api.coindcx.com"

PROFIT_TARGET = 2.5 / 100
STOP_LOSS = 3.0 / 100
DAILY_LOSS_LIMIT = 10.0 / 100
COOLDOWN_LOSSES = 5
COOLDOWN_TIME = 3600

position = None
buy_price = 0
btc_quantity = 0
starting_capital = None
daily_start_capital = None
daily_reset_time = datetime.now() + timedelta(hours=24)
consecutive_losses = 0
cooldown_until = None
price_history = []

def get_signature(body):
    return hmac.new(
        API_SECRET.encode(),
        body.encode(),
        hashlib.sha256
    ).hexdigest()

def get_headers(body):
    return {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": get_signature(body)
    }

def get_inr_balance():
    try:
        body = json.dumps({"timestamp": int(time.time() * 1000)})
        res = requests.post(
            f"{BASE_URL}/exchange/v1/users/balances",
            data=body,
            headers=get_headers(body)
        )
        data = res.json()
        if isinstance(data, list):
            for b in data:
                if isinstance(b, dict) and b.get("currency") == "INR":
                    return float(b.get("balance", 0))
        return 0
    except Exception as e:
        print(f"Balance error: {e}")
        return 0

def get_price():
    try:
        res = requests.get("https://api.coindcx.com/exchange/ticker")
        data = res.json()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("market") == SYMBOL:
                    return float(item.get("last_price", 0))
        elif isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict) and val.get("market") == SYMBOL:
                    return float(val.get("last_price", 0))
    except Exception as e:
        print(f"Price error: {e}")
    return None

def place_buy_order(inr_amount, current_price):
    try:
        quantity = round(inr_amount / current_price, 6)
        timestamp = int(time.time() * 1000)
        order = {
            "side": "buy",
            "order_type": "market_order",
            "market": SYMBOL,
            "quantity": quantity,
            "timestamp": timestamp
        }
        body = json.dumps(order)
        res = requests.post(
            f"{BASE_URL}/exchange/v1/orders/create",
            data=body,
            headers=get_headers(body)
        )
        print(f"BUY: {quantity} BTC | INR: {inr_amount:.2f}")
        return quantity
    except Exception as e:
        print(f"Buy error: {e}")
        return 0

def place_sell_order(quantity):
    try:
        timestamp = int(time.time() * 1000)
        order = {
            "side": "sell",
            "order_type": "market_order",
            "market": SYMBOL,
            "quantity": quantity,
            "timestamp": timestamp
        }
        body = json.dumps(order)
        res = requests.post(
            f"{BASE_URL}/exchange/v1/orders/create",
            data=body,
            headers=get_headers(body)
        )
        print(f"SELL: {quantity} BTC")
        return res.json()
    except Exception as e:
        print(f"Sell error: {e}")

def get_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def get_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = prices[-i] - prices[-i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

print("MoneyAgent Bot Started!")
print(f"Profit={PROFIT_TARGET*100}% | StopLoss={STOP_LOSS*100}% | DailyLimit={DAILY_LOSS_LIMIT*100}%")

while True:
    try:
        now = datetime.now()

        if cooldown_until and now < cooldown_until:
            remaining = (cooldown_until - now).seconds // 60
            print(f"Cooldown: {remaining} min baki")
            time.sleep(60)
            continue

        if now >= daily_reset_time:
            daily_start_capital = get_inr_balance()
            daily_reset_time = now + timedelta(hours=24)
            consecutive_losses = 0
            print(f"Daily reset! Capital: {daily_start_capital}")

        inr_balance = get_inr_balance()

        if starting_capital is None:
            starting_capital = inr_balance
            daily_start_capital = inr_balance
            print(f"Starting Capital: {starting_capital}")

        if daily_start_capital > 0:
            daily_loss = (daily_start_capital - inr_balance) / daily_start_capital
            if daily_loss >= DAILY_LOSS_LIMIT and position is None:
                print(f"Daily loss limit! Trading band!")
                time.sleep(300)
                continue

        price = get_price()
        if not price:
            print("Price nahi mili!")
            time.sleep(60)
            continue

        price_history.append(price)
        if len(price_history) > 120:
            price_history.pop(0)

        ema9 = get_ema(price_history, 9)
        ema21 = get_ema(price_history, 21)
        rsi = get_rsi(price_history)

        if not ema9 or not ema21:
            print(f"Price: {price:,.0f} | Data collect ho raha hai {len(price_history)}/21")
            time.sleep(60)
            continue

        rsi_str = f"{rsi:.1f}" if rsi else "..."
        print(f"Price: {price:,.0f} | EMA9: {ema9:,.0f} | EMA21: {ema21:,.0f} | RSI: {rsi_str} | INR: {inr_balance:.2f}")

        if position == "buy":
            profit_pct = (price - buy_price) / buy_price
            if profit_pct >= PROFIT_TARGET:
                print(f"Profit! +{profit_pct*100:.2f}% SELL!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses = 0
            elif profit_pct <= -STOP_LOSS:
                print(f"Stop Loss! {profit_pct*100:.2f}% SELL!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses += 1
                if consecutive_losses >= COOLDOWN_LOSSES:
                    cooldown_until = now + timedelta(seconds=COOLDOWN_TIME)
                    print("5 loss! Cooldown shuru!")

        elif position is None and inr_balance > 10:
            ema_ok = ema9 > ema21
            rsi_ok = rsi and rsi < 60
            if ema_ok and rsi_ok:
                print(f"BUY Signal!")
                btc_quantity = place_buy_order(inr_balance, price)
                position = "buy"
                buy_price = price
            else:
                print(f"Wait | EMA: {'OK' if ema_ok else 'NO'} | RSI: {rsi_str}")

        time.sleep(60)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(30)
