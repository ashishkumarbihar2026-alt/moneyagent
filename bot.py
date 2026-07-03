import requests
import hashlib
import hmac
import time
import json
from datetime import datetime, timedelta

# === CONFIG ===
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
SYMBOL = "BTCINR"
BASE_URL = "https://api.coindcx.com"

# === RULES ===
PROFIT_TARGET = 2.5 / 100
STOP_LOSS = 3.0 / 100
DAILY_LOSS_LIMIT = 10.0 / 100
COOLDOWN_LOSSES = 5
COOLDOWN_TIME = 3600

# === STATE ===
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
    body = json.dumps({"timestamp": int(time.time() * 1000)})
    res = requests.post(
        f"{BASE_URL}/exchange/v1/users/balances",
        data=body,
        headers=get_headers(body)
    )
    balances = res.json()
    for b in balances:
        if b["currency"] == "INR":
            return float(b["balance"])
    return 0

def get_btc_balance():
    body = json.dumps({"timestamp": int(time.time() * 1000)})
    res = requests.post(
        f"{BASE_URL}/exchange/v1/users/balances",
        data=body,
        headers=get_headers(body)
    )
    balances = res.json()
    for b in balances:
        if b["currency"] == "BTC":
            return float(b["balance"])
    return 0

def get_price():
    res = requests.get(f"{BASE_URL}/exchange/v1/markets_details")
    for market in res.json():
        if market["pair"] == SYMBOL:
            return float(market["last_price"])
    return None

def place_buy_order(inr_amount, current_price):
    # INR se BTC quantity calculate karo
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
    print(f"BUY: {quantity} BTC @ ₹{current_price:,.0f} | INR: ₹{inr_amount:.2f}")
    return quantity

def place_sell_order(quantity):
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
    print(f"SELL: {quantity} BTC @ market price")
    return res.json()

def get_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

print("MoneyAgent Bot Started! 🚀")
print(f"Rules: Profit={PROFIT_TARGET*100}% | StopLoss={STOP_LOSS*100}% | DailyLimit={DAILY_LOSS_LIMIT*100}%")

while True:
    try:
        now = datetime.now()

        # Cooldown check
        if cooldown_until and now < cooldown_until:
            remaining = (cooldown_until - now).seconds // 60
            print(f"⏳ Cooldown: {remaining} minute baki hain...")
            time.sleep(60)
            continue

        # Daily reset
        if now >= daily_reset_time:
            print("🔄 24 ghante complete — Daily reset!")
            daily_start_capital = get_inr_balance()
            daily_reset_time = now + timedelta(hours=24)
            consecutive_losses = 0
            print(f"Naya daily capital: ₹{daily_start_capital}")

        # Balance lo
        inr_balance = get_inr_balance()

        # Starting capital
        if starting_capital is None:
            starting_capital = inr_balance
            daily_start_capital = inr_balance
            print(f"Starting Capital: ₹{starting_capital}")

        # Rule 5: Daily 10% loss check
        if daily_start_capital > 0:
            daily_loss = (daily_start_capital - inr_balance) / daily_start_capital
            if daily_loss >= DAILY_LOSS_LIMIT and position is None:
                print(f"🚫 Daily loss limit! ({daily_loss*100:.1f}%) — Aaj trading band!")
                time.sleep(300)
                continue

        # Price lo
        price = get_price()
        if not price:
            time.sleep(60)
            continue

        price_history.append(price)
        if len(price_history) > 50:
            price_history.pop(0)

        ema9 = get_ema(price_history, 9)
        ema21 = get_ema(price_history, 21)

        if not ema9 or not ema21:
            print(f"Price: ₹{price:,.0f} | Data collect ho raha hai...")
            time.sleep(60)
            continue

        print(f"Price: ₹{price:,.0f} | EMA9: ₹{ema9:,.0f} | EMA21: ₹{ema21:,.0f} | INR: ₹{inr_balance:.2f}")

        # === OPEN POSITION CHECK ===
        if position == "buy":
            profit_pct = (price - buy_price) / buy_price

            # Rule 1: 2.5% profit
            if profit_pct >= PROFIT_TARGET:
                print(f"✅ Profit! +{profit_pct*100:.2f}% — SELLING!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses = 0
                profit_inr = btc_quantity * (price - buy_price)
                print(f"💰 Profit: ₹{profit_inr:.2f}")

            # Rule 2: 3% stop loss
            elif profit_pct <= -STOP_LOSS:
                print(f"🛑 Stop Loss! {profit_pct*100:.2f}% — SELLING!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses += 1
                loss_inr = btc_quantity * (buy_price - price)
                print(f"❌ Loss: ₹{loss_inr:.2f}")

                # Rule 6: Cooldown
                if consecutive_losses >= COOLDOWN_LOSSES:
                    cooldown_until = now + timedelta(seconds=COOLDOWN_TIME)
                    print(f"⏳ 5 baar loss! 1 ghante cooldown shuru!")

        # === NEW TRADE ===
        elif position is None:
            if ema9 > ema21 and inr_balance > 10:
                print(f"🟢 BUY Signal! ₹{inr_balance:.2f} se BTC kharid raha hoon!")
                btc_quantity = place_buy_order(inr_balance, price)
                position = "buy"
                buy_price = price

        time.sleep(60)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(30)
