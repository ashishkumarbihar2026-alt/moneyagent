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
volume_history = []

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
    for b in res.json():
        if b["currency"] == "INR":
            return float(b["balance"])
    return 0

def get_price_and_volume():
    res = requests.get(f"{BASE_URL}/exchange/v1/markets_details")
    for market in res.json():
    if isinstance(market, dict) and market.get("pair") == SYMBOL:
        price = float(market.get("last_price", 0))
        volume = float(market.get("volume", 0))
        return price, volume
    return None, None

def place_buy_order(inr_amount, current_price):
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

def get_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
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

def get_1hour_trend(prices):
    # 1 ghante mein 60 readings (har 1 min mein)
    if len(prices) < 60:
        return None
    hour_ago_price = prices[-60]
    current_price = prices[-1]
    trend = (current_price - hour_ago_price) / hour_ago_price * 100
    return trend

def get_avg_volume(volumes, period=20):
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period

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

        # Balance
        inr_balance = get_inr_balance()

        if starting_capital is None:
            starting_capital = inr_balance
            daily_start_capital = inr_balance
            print(f"Starting Capital: ₹{starting_capital}")

        # Daily loss check
        if daily_start_capital > 0:
            daily_loss = (daily_start_capital - inr_balance) / daily_start_capital
            if daily_loss >= DAILY_LOSS_LIMIT and position is None:
                print(f"🚫 Daily loss limit! ({daily_loss*100:.1f}%) — Aaj trading band!")
                time.sleep(300)
                continue

        # Price + Volume
        price, volume = get_price_and_volume()
        if not price:
            time.sleep(60)
            continue

        price_history.append(price)
        volume_history.append(volume)
        if len(price_history) > 120:
            price_history.pop(0)
        if len(volume_history) > 120:
            volume_history.pop(0)

        # Indicators
        ema9 = get_ema(price_history, 9)
        ema21 = get_ema(price_history, 21)
        rsi = get_rsi(price_history)
        trend_1h = get_1hour_trend(price_history)
        avg_volume = get_avg_volume(volume_history)
        current_volume = volume_history[-1] if volume_history else 0

        if not ema9 or not ema21:
            print(f"Price: ₹{price:,.0f} | Data collect ho raha hai... ({len(price_history)}/21)")
            time.sleep(60)
            continue

        # Log
        rsi_str = f"{rsi:.1f}" if rsi else "..."
        trend_str = f"{trend_1h:.2f}%" if trend_1h else "..."
        print(f"Price: ₹{price:,.0f} | EMA9: ₹{ema9:,.0f} | EMA21: ₹{ema21:,.0f} | RSI: {rsi_str} | 1H Trend: {trend_str} | Balance: ₹{inr_balance:.2f}")

        # === OPEN POSITION ===
        if position == "buy":
            profit_pct = (price - buy_price) / buy_price

            if profit_pct >= PROFIT_TARGET:
                print(f"✅ Profit! +{profit_pct*100:.2f}% — SELLING!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses = 0
                profit_inr = btc_quantity * (price - buy_price)
                print(f"💰 Profit: ₹{profit_inr:.2f}")

            elif profit_pct <= -STOP_LOSS:
                print(f"🛑 Stop Loss! {profit_pct*100:.2f}% — SELLING!")
                place_sell_order(btc_quantity)
                position = None
                consecutive_losses += 1
                loss_inr = btc_quantity * (buy_price - price)
                print(f"❌ Loss: ₹{loss_inr:.2f}")

                if consecutive_losses >= COOLDOWN_LOSSES:
                    cooldown_until = now + timedelta(seconds=COOLDOWN_TIME)
                    print(f"⏳ 5 baar loss! 1 ghante cooldown!")

        # === NEW TRADE ===
        elif position is None and inr_balance > 10:

            # BUY conditions
            ema_ok = ema9 > ema21
            rsi_ok = rsi and rsi < 60        # RSI: overbought nahi
            trend_ok = trend_1h and trend_1h > 0  # 1H trend upar
            volume_ok = avg_volume and current_volume > avg_volume  # Volume average se zyada

            if ema_ok and rsi_ok and trend_ok and volume_ok:
                print(f"🟢 BUY Signal!")
                print(f"   EMA: ✅ | RSI: {rsi:.1f} ✅ | 1H: {trend_1h:.2f}% ✅ | Volume: ✅")
                btc_quantity = place_buy_order(inr_balance, price)
                position = "buy"
                buy_price = price
            else:
                reasons = []
                if not ema_ok: reasons.append("EMA ❌")
                if not rsi_ok: reasons.append(f"RSI {rsi:.1f} ❌")
                if not trend_ok: reasons.append(f"1H {trend_str} ❌")
                if not volume_ok: reasons.append("Volume ❌")
                print(f"⏳ Wait | {' | '.join(reasons)}")

        time.sleep(60)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(30)
