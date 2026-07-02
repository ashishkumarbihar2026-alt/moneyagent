import requests
import hashlib
import hmac
import time
import json

# === CONFIG ===
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
SYMBOL = "BTCINR"
TRADE_AMOUNT = 500  # INR amount per trade

# === CoinDCX API ===
BASE_URL = "https://api.coindcx.com"

def get_signature(body, secret):
    return hmac.new(
        secret.encode(),
        body.encode(),
        hashlib.sha256
    ).hexdigest()

def get_balance():
    body = json.dumps({"timestamp": int(time.time() * 1000)})
    sig = get_signature(body, API_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": sig
    }
    res = requests.post(f"{BASE_URL}/exchange/v1/users/balances", 
                        data=body, headers=headers)
    return res.json()

def place_order(side, price=None):
    timestamp = int(time.time() * 1000)
    order = {
        "side": side,
        "order_type": "market_order",
        "market": SYMBOL,
        "total_quantity": TRADE_AMOUNT,
        "timestamp": timestamp
    }
    body = json.dumps(order)
    sig = get_signature(body, API_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": sig
    }
    res = requests.post(f"{BASE_URL}/exchange/v1/orders/create",
                        data=body, headers=headers)
    print(f"Order placed: {side} | Response: {res.json()}")
    return res.json()

def get_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def get_prices():
    res = requests.get(f"{BASE_URL}/exchange/v1/markets_details")
    data = res.json()
    for market in data:
        if market["pair"] == SYMBOL:
            return float(market["last_price"])
    return None

price_history = []
position = None  # "buy" or None

print("MoneyAgent Bot Started! 🚀")

while True:
    try:
        price = get_prices()
        if price:
            price_history.append(price)
            if len(price_history) > 50:
                price_history.pop(0)

            ema9 = get_ema(price_history, 9)
            ema21 = get_ema(price_history, 21)

            print(f"Price: {price} | EMA9: {ema9:.2f} | EMA21: {ema21:.2f}")

            # BUY signal
            if ema9 and ema21 and ema9 > ema21 and position != "buy":
                print("BUY Signal! 🟢")
                place_order("buy")
                position = "buy"

            # SELL signal
            elif ema9 and ema21 and ema9 < ema21 and position == "buy":
                print("SELL Signal! 🔴")
                place_order("sell")
                position = None

        time.sleep(60)  # Har 1 minute mein check karta hai

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(30)
