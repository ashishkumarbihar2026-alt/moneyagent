import requests
import hashlib
import hmac
import time
import json
import math
import os
from datetime import datetime, timedelta

API_KEY = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL = "https://api.coindcx.com"

# ---- Multi-coin list: add/remove coins here ----
SYMBOLS = ["BTCINR", "ETHINR"]

PRECISION_MAP = {
    "BTCINR": 5,
    "ETHINR": 4,
    "HMSTRINR": 0
}

PROFIT_TARGET = 2.0 / 100
STOP_LOSS = 3.0 / 100
TRAILING_STOP_PCT = 1.0 / 100   # once in profit, sell if price falls this much from peak
DAILY_LOSS_LIMIT = 10.0 / 100
COOLDOWN_LOSSES = 5
COOLDOWN_TIME = 3600

# ---- Telegram alerts (optional, set these in Railway Variables) ----
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")

# ---- Per-symbol state ----
positions = {
    sym: {"position": None, "buy_price": 0, "quantity": 0, "peak_price": 0}
    for sym in SYMBOLS
}
price_history = {sym: [] for sym in SYMBOLS}

starting_capital = None
daily_start_capital = None
daily_reset_time = datetime.now() + timedelta(hours=24)
consecutive_losses = 0
cooldown_until = None

STATE_FILE = "/data/position.json" if os.path.isdir("/data") else "position.json"

def save_state():
    try:
        state = {
            "positions": positions,
            "starting_capital": starting_capital,
            "daily_start_capital": daily_start_capital,
            "daily_reset_time": daily_reset_time.isoformat(),
            "consecutive_losses": consecutive_losses,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save error: {e}")

def load_state():
    global positions, starting_capital, daily_start_capital, daily_reset_time, consecutive_losses, cooldown_until
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        saved_positions = state.get("positions", {})
        for sym in SYMBOLS:
            if sym in saved_positions:
                positions[sym] = saved_positions[sym]
        starting_capital = state.get("starting_capital")
        daily_start_capital = state.get("daily_start_capital")
        if state.get("daily_reset_time"):
            daily_reset_time = datetime.fromisoformat(state["daily_reset_time"])
        consecutive_losses = state.get("consecutive_losses", 0)
        if state.get("cooldown_until"):
            cooldown_until = datetime.fromisoformat(state["cooldown_until"])
        print(f"State restored: {positions}")
    except FileNotFoundError:
        print("No saved state found, starting fresh")
    except Exception as e:
        print(f"State load error: {e}")

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
            headers=get_headers(body),
            timeout=15
        )
        if res.status_code != 200:
            print(f"Balance error: HTTP {res.status_code}")
            return 0
        data = res.json()
        if isinstance(data, list):
            for b in data:
                if isinstance(b, dict) and b.get("currency") == "INR":
                    return float(b.get("balance", 0))
        return 0
    except Exception as e:
        print(f"Balance error: {e}")
        return 0

def get_all_prices():
    """Fetch ticker once, return {symbol: price} for all SYMBOLS."""
    prices = {}
    try:
        res = requests.get("https://api.coindcx.com/exchange/ticker", timeout=15)
        if res.status_code != 200:
            print(f"Price error: HTTP {res.status_code}")
            return prices
        data = res.json()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("market") in SYMBOLS:
                    prices[item["market"]] = float(item.get("last_price", 0))
    except Exception as e:
        print(f"Price error: {e}")
    return prices

def place_buy_order(symbol, inr_amount, current_price):
    try:
        usable_amount = inr_amount * 0.97
        precision = PRECISION_MAP.get(symbol, 2)
        multiplier = 10 ** precision
        quantity = math.floor((usable_amount / current_price) * multiplier) / multiplier
        if quantity <= 0:
            return 0
        timestamp = int(time.time() * 1000)
        order = {
            "side": "buy",
            "order_type": "market_order",
            "market": symbol,
            "total_quantity": quantity,
            "timestamp": timestamp
        }
        body = json.dumps(order)
        res = requests.post(
            f"{BASE_URL}/exchange/v1/orders/create",
            data=body,
            headers=get_headers(body)
        )
        print(f"BUY: {quantity} {symbol} | INR: {inr_amount:.2f}")
        response = res.json()
        print(f"Order response: {response}")
        return quantity
    except Exception as e:
        print(f"Buy error: {e}")
        return 0

def place_sell_order(symbol, quantity):
    try:
        timestamp = int(time.time() * 1000)
        order = {
            "side": "sell",
            "order_type": "market_order",
            "market": symbol,
            "total_quantity": quantity,
            "timestamp": timestamp
        }
        body = json.dumps(order)
        res = requests.post(
            f"{BASE_URL}/exchange/v1/orders/create",
            data=body,
            headers=get_headers(body)
        )
        print(f"SELL: {quantity} {symbol}")
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
print(f"Coins: {SYMBOLS} | Profit:{PROFIT_TARGET*100}% | StopLoss:{STOP_LOSS*100}% | Trailing:{TRAILING_STOP_PCT*100}% | DailyLossLimit:{DAILY_LOSS_LIMIT*100}%")
send_telegram(f"MoneyAgent Bot Started! Coins: {SYMBOLS}")

load_state()

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
            save_state()

        inr_balance = get_inr_balance()

        if starting_capital is None:
            starting_capital = inr_balance
            daily_start_capital = inr_balance
            print(f"Starting Capital: {starting_capital}")
            save_state()

        if daily_start_capital and daily_start_capital > 0:
            daily_loss = (daily_start_capital - inr_balance) / daily_start_capital
            has_open_position = any(p["position"] == "buy" for p in positions.values())
            if daily_loss >= DAILY_LOSS_LIMIT and not has_open_position:
                print("Daily loss limit! Trading band!")
                send_telegram("Daily loss limit hit! Trading paused for 5 min.")
                time.sleep(300)
                continue

        all_prices = get_all_prices()

        # Count symbols without open positions (for splitting available cash)
        pending_buys = [s for s in SYMBOLS if positions[s]["position"] is None]

        for symbol in SYMBOLS:
            price = all_prices.get(symbol)
            if not price:
                continue

            price_history[symbol].append(price)
            if len(price_history[symbol]) > 120:
                price_history[symbol].pop(0)

            hist = price_history[symbol]
            ema9 = get_ema(hist, 9)
            ema21 = get_ema(hist, 21)
            rsi = get_rsi(hist)

            if not ema9 or not ema21:
                print(f"{symbol} | Price: {price:.4f} | Data collect ho raha hai {len(hist)}/21")
                continue

            rsi_str = f"{rsi:.1f}" if rsi else "..."
            pos = positions[symbol]

            if pos["position"] == "buy":
                if price > pos["peak_price"]:
                    pos["peak_price"] = price

                profit_pct = (price - pos["buy_price"]) / pos["buy_price"]
                drop_from_peak = (pos["peak_price"] - price) / pos["peak_price"] if pos["peak_price"] > 0 else 0

                print(f"{symbol} | Price: {price:.4f} | EMA9: {ema9:.4f} | EMA21: {ema21:.4f} | RSI: {rsi_str} | P/L: {profit_pct*100:.2f}%")

                if profit_pct >= PROFIT_TARGET:
                    print(f"{symbol} Profit! {profit_pct*100:.2f}% SELL!")
                    place_sell_order(symbol, pos["quantity"])
                    send_telegram(f"{symbol} PROFIT SELL! {profit_pct*100:.2f}%")
                    pos["position"] = None
                    pos["quantity"] = 0
                    pos["peak_price"] = 0
                    consecutive_losses = 0
                    save_state()
                elif profit_pct > 0 and drop_from_peak >= TRAILING_STOP_PCT:
                    print(f"{symbol} Trailing Stop! Locking {profit_pct*100:.2f}% SELL!")
                    place_sell_order(symbol, pos["quantity"])
                    send_telegram(f"{symbol} TRAILING STOP SELL! {profit_pct*100:.2f}%")
                    pos["position"] = None
                    pos["quantity"] = 0
                    pos["peak_price"] = 0
                    consecutive_losses = 0
                    save_state()
                elif profit_pct <= -STOP_LOSS:
                    print(f"{symbol} Stop Loss! {profit_pct*100:.2f}% SELL!")
                    place_sell_order(symbol, pos["quantity"])
                    send_telegram(f"{symbol} STOP LOSS SELL! {profit_pct*100:.2f}%")
                    pos["position"] = None
                    pos["quantity"] = 0
                    pos["peak_price"] = 0
                    consecutive_losses += 1
                    if consecutive_losses >= COOLDOWN_LOSSES:
                        cooldown_until = now + timedelta(seconds=COOLDOWN_TIME)
                        print("5 loss! Cooldown shuru!")
                        send_telegram("5 consecutive losses! Cooldown for 1 hour.")
                    save_state()

            elif inr_balance > 10 and symbol in pending_buys:
                ema_ok = ema9 > ema21
                rsi_ok = rsi and rsi < 60
                print(f"{symbol} | Price: {price:.4f} | EMA: {'OK' if ema_ok else 'NO'} | RSI: {rsi_str}")
                if ema_ok and rsi_ok:
                    share = inr_balance / len(pending_buys) if pending_buys else inr_balance
                    print(f"{symbol} BUY Signal!")
                    quantity = place_buy_order(symbol, share, price)
                    if quantity > 0:
                        pos["position"] = "buy"
                        pos["buy_price"] = price
                        pos["quantity"] = quantity
                        pos["peak_price"] = price
                        send_telegram(f"{symbol} BUY! {quantity} @ {price:.4f}")
                        save_state()
                        pending_buys.remove(symbol)
                        inr_balance -= share

        time.sleep(60)

    except Exception as e:
        print(f"Error: {e}")
        send_telegram(f"Bot error: {e}")
        time.sleep(30)
