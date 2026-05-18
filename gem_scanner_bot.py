"""
╔══════════════════════════════════════════════════════╗
║         DEGEN GEM SCANNER BOT — by Richard           ║
║   24/7 meme coin scanner using DexScreener +         ║
║   RugCheck APIs. Sends Telegram alerts on finds.     ║
╚══════════════════════════════════════════════════════╝

SETUP:
  1. pip install -r requirements.txt
  2. Create a bot via @BotFather on Telegram → copy token
  3. Get your chat ID via @userinfobot on Telegram
  4. Set env vars:
       export TELEGRAM_BOT_TOKEN="your_token_here"
       export TELEGRAM_CHAT_ID="your_chat_id_here"
  5. Run: python gem_scanner_bot.py
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
#  CONFIG  —  edit these or set as env vars
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = "8886586492:AAGaCqAlASothECJGp-2g3zeGuNrpKOkubI"
CHAT_ID        = "6969123971"

SCAN_INTERVAL_SECONDS = 300   # scan every 5 minutes
MIN_ALERT_SCORE       = 45    # only alert if coin scores ≥ this (0–100)

# Active chains to monitor
ACTIVE_CHAINS = ["solana", "base", "ethereum", "bsc"]

# ─────────────────────────────────────────────
#  FILTER SETTINGS  —  tune to your risk level
# ─────────────────────────────────────────────

FILTERS = {
    "min_liquidity_usd":  5_000,       # minimum pool liquidity
    "max_liquidity_usd":  2_000_000,   # ignore huge established coins
    "min_fdv":            20_000,      # minimum market cap
    "max_fdv":            5_000_000,   # sweet spot ceiling
    "min_volume_h24":     5_000,       # needs real trading activity
    "min_txns_h1":        10,          # at least 10 txns in last hour
    "max_age_hours":      24,          # newly launched only
}

# ─────────────────────────────────────────────
#  GLOBALS
# ─────────────────────────────────────────────

seen_pairs: set[str] = set()
bot_start_time = datetime.now(timezone.utc)
total_scans = 0
total_alerts_sent = 0

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  API HELPERS
# ─────────────────────────────────────────────

HEADERS = {"User-Agent": "GemScannerBot/1.0"}

def _get(url: str, timeout: int = 10):
    """Safe GET wrapper — returns parsed JSON or None."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f"GET failed {url}: {e}")
    return None


def fetch_latest_profiles() -> list[dict]:
    """
    DexScreener /token-profiles/latest — returns recently added tokens
    across all chains. Good hunting ground for new launches.
    """
    data = _get("https://api.dexscreener.com/token-profiles/latest/v1")
    if isinstance(data, list):
        return data
    return []


def fetch_boosted_tokens() -> list[dict]:
    """
    DexScreener /token-boosts/latest — tokens currently being boosted.
    Boosted ≠ scam; sometimes early projects buy visibility legitimately.
    """
    data = _get("https://api.dexscreener.com/token-boosts/latest/v1")
    if isinstance(data, list):
        return data
    return []


def fetch_token_pairs(token_address: str, chain: str) -> list[dict]:
    """Get all DEX pairs for a given token address."""
    data = _get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
    if not data:
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if p.get("chainId") == chain]


def fetch_rugcheck(mint_address: str) -> dict | None:
    """
    RugCheck.xyz summary for Solana tokens.
    Returns risk data including score (higher = safer) and risk list.
    """
    return _get(f"https://api.rugcheck.xyz/v1/tokens/{mint_address}/report/summary")


def fetch_pair_age_hours(pair: dict) -> float:
    """
    Estimate age of a trading pair in hours using pairCreatedAt timestamp.
    Returns a large number (9999) if unknown so we don't accidentally filter it out.
    """
    created_at = pair.get("pairCreatedAt")
    if created_at:
        created_ts = created_at / 1000  # ms → seconds
        age_seconds = time.time() - created_ts
        return age_seconds / 3600
    return 9999.0


# ─────────────────────────────────────────────
#  SCORING ENGINE
# ─────────────────────────────────────────────

def score_coin(pair: dict, rugcheck: dict | None) -> tuple[int, list[str], list[str]]:
    """
    Score a coin 0–100 based on on-chain signals.
    Returns (score, green_flags, red_flags).
    A score ≥ 60 is a strong find. 40–59 is watchlist material.
    """
    score = 0
    green = []
    red   = []

    # ── Liquidity ──────────────────────────────
    liquidity = (pair.get("liquidity") or {}).get("usd") or 0
    if liquidity >= 50_000:
        score += 20
        green.append(f"✅ Strong liquidity: ${liquidity:,.0f}")
    elif liquidity >= 15_000:
        score += 14
        green.append(f"✅ Decent liquidity: ${liquidity:,.0f}")
    elif liquidity >= 5_000:
        score += 7
        green.append(f"⚠️ Low-ish liquidity: ${liquidity:,.0f}")
    else:
        score -= 10
        red.append(f"🔴 Very low liquidity: ${liquidity:,.0f}")

    # ── Volume (24h) ────────────────────────────
    vol_24h = (pair.get("volume") or {}).get("h24") or 0
    if vol_24h >= 100_000:
        score += 20
        green.append(f"✅ High 24h volume: ${vol_24h:,.0f}")
    elif vol_24h >= 30_000:
        score += 13
        green.append(f"✅ Good 24h volume: ${vol_24h:,.0f}")
    elif vol_24h >= 5_000:
        score += 6
        green.append(f"⚠️ Light volume: ${vol_24h:,.0f}")
    else:
        red.append(f"🔴 Very low volume: ${vol_24h:,.0f}")

    # ── Buy / Sell pressure (1h txns) ───────────
    txns_h1  = (pair.get("txns") or {}).get("h1") or {}
    h1_buys  = txns_h1.get("buys",  0)
    h1_sells = txns_h1.get("sells", 0)
    total_h1 = h1_buys + h1_sells
    if total_h1 > 0:
        buy_ratio = h1_buys / total_h1
        if buy_ratio >= 0.65:
            score += 18
            green.append(f"✅ Strong buy pressure: {buy_ratio:.0%} buys (1h)")
        elif buy_ratio >= 0.50:
            score += 10
            green.append(f"⚠️ Balanced: {buy_ratio:.0%} buys (1h)")
        else:
            score -= 8
            red.append(f"🔴 Sell pressure dominant: {buy_ratio:.0%} buys (1h)")
    else:
        red.append("🔴 No 1h transaction data")

    # ── Price momentum ──────────────────────────
    pc = pair.get("priceChange") or {}
    chg_1h  = pc.get("h1")  or 0
    chg_24h = pc.get("h24") or 0

    if 5 <= chg_1h <= 80:
        score += 12
        green.append(f"✅ Healthy 1h gain: +{chg_1h:.1f}%")
    elif chg_1h > 80:
        score += 4
        red.append(f"⚠️ Parabolic 1h: +{chg_1h:.1f}% — may be late")
    elif chg_1h < -20:
        score -= 10
        red.append(f"🔴 Heavy 1h dump: {chg_1h:.1f}%")

    if chg_24h > 0:
        score += 5
        green.append(f"✅ Positive 24h trend: +{chg_24h:.1f}%")

    # ── Market cap / FDV sweet spot ─────────────
    fdv = pair.get("fdv") or 0
    if 50_000 <= fdv <= 300_000:
        score += 15
        green.append(f"✅ Low MC sweet spot: ${fdv:,.0f}")
    elif 300_000 < fdv <= 1_000_000:
        score += 8
        green.append(f"⚠️ Mid MC: ${fdv:,.0f}")
    elif fdv < 50_000 and fdv > 0:
        score += 3
        red.append(f"⚠️ Ultra micro cap: ${fdv:,.0f} — high risk")
    elif fdv > 1_000_000:
        score -= 5
        red.append(f"⚠️ High MC: ${fdv:,.0f} — limited upside")

    # ── Age (newer = more degen upside) ─────────
    age_h = fetch_pair_age_hours(pair)
    if age_h <= 2:
        score += 10
        green.append(f"✅ Very fresh: {age_h:.1f}h old")
    elif age_h <= 8:
        score += 6
        green.append(f"✅ Fresh: {age_h:.1f}h old")
    elif age_h <= 24:
        score += 2
        green.append(f"⚠️ Still new: {age_h:.1f}h old")
    else:
        red.append(f"⚠️ Older token: {age_h:.0f}h old")

    # ── RugCheck (Solana only) ───────────────────
    if rugcheck:
        rc_score = rugcheck.get("score") or 0
        risks    = rugcheck.get("risks") or []
        dangers  = [r for r in risks if r.get("level") == "danger"]
        warnings = [r for r in risks if r.get("level") == "warn"]

        if rc_score >= 800:
            score += 15
            green.append(f"✅ RugCheck safe: {rc_score}/1000")
        elif rc_score >= 500:
            score += 7
            green.append(f"⚠️ RugCheck moderate: {rc_score}/1000")
        else:
            score -= 20
            red.append(f"🔴 RugCheck risky: {rc_score}/1000")

        for d in dangers[:2]:
            score -= 15
            red.append(f"🔴 DANGER: {d.get('name', 'Unknown')}")
        for w in warnings[:2]:
            score -= 5
            red.append(f"⚠️ WARNING: {w.get('name', 'Unknown')}")

    # Clamp to 0–100
    score = max(0, min(100, score))
    return score, green, red


# ─────────────────────────────────────────────
#  MESSAGE FORMATTER
# ─────────────────────────────────────────────

def format_alert(pair: dict, score: int, green: list[str], red: list[str]) -> str:
    chain        = pair.get("chainId", "?").upper()
    base         = pair.get("baseToken") or {}
    name         = base.get("name",    "Unknown")
    symbol       = base.get("symbol",  "???")
    address      = base.get("address", "")
    pair_address = pair.get("pairAddress", "")
    dex_id       = pair.get("dexId",   "dex")

    price_usd = pair.get("priceUsd") or "0"
    fdv       = pair.get("fdv")      or 0
    liq       = (pair.get("liquidity") or {}).get("usd") or 0
    vol_24h   = (pair.get("volume")    or {}).get("h24") or 0
    pc        = pair.get("priceChange") or {}
    chg_1h    = pc.get("h1")  or 0
    chg_24h   = pc.get("h24") or 0

    age_h = fetch_pair_age_hours(pair)
    age_str = f"{age_h:.1f}h" if age_h < 9999 else "unknown"

    # Grade
    if score >= 75:
        badge = "🔥🔥🔥 STRONG BUY"
    elif score >= 60:
        badge = "🔥🔥 SOLID PLAY"
    elif score >= 45:
        badge = "🔥 ON WATCH"
    else:
        badge = "⚠️ WEAK"

    sign_1h  = "+" if chg_1h  >= 0 else ""
    sign_24h = "+" if chg_24h >= 0 else ""

    dex_url = f"https://dexscreener.com/{pair.get('chainId','')}/{pair_address}"

    green_block = "\n".join(green) if green else "—"
    red_block   = "\n".join(red)   if red   else "None ✅"

    return f"""
🚨 *GEM ALERT — {badge}*
━━━━━━━━━━━━━━━━━━━━━━
*{name}* (${symbol}) on {chain} via {dex_id.upper()}
🏆 *Score: {score}/100*

💵 Price: `${float(price_usd):.10f}`
📊 FDV/MC: `${fdv:,.0f}`
💧 Liquidity: `${liq:,.0f}`
📦 24h Volume: `${vol_24h:,.0f}`
🕐 1h: `{sign_1h}{chg_1h:.1f}%` | 24h: `{sign_24h}{chg_24h:.1f}%`
⏱ Age: `{age_str}`

*✅ GREEN FLAGS:*
{green_block}

*🔴 RED FLAGS:*
{red_block}

🔗 [View Chart on DexScreener]({dex_url})
📋 CA: `{address}`
━━━━━━━━━━━━━━━━━━━━━━
⚠️ _DYOR. Not financial advice._
""".strip()


# ─────────────────────────────────────────────
#  CORE SCANNER
# ─────────────────────────────────────────────

async def run_scan(app: Application) -> int:
    """
    Full scan cycle:
    1. Fetch latest token profiles from DexScreener
    2. Also pull boosted tokens (sometimes early legit projects)
    3. Apply hard filters (liquidity, volume, age, chain)
    4. Run RugCheck on Solana tokens
    5. Score each coin
    6. Send Telegram alert if score ≥ MIN_ALERT_SCORE
    """
    global seen_pairs, total_scans, total_alerts_sent
    total_scans += 1
    alerts_this_scan = 0

    log.info(f"[SCAN #{total_scans}] Starting...")

    # Gather candidates from two sources
    profiles = fetch_latest_profiles()[:80]
    boosted  = fetch_boosted_tokens()[:40]

    # Combine and deduplicate by tokenAddress
    candidates = {p.get("tokenAddress"): p for p in profiles + boosted if p.get("tokenAddress")}

    log.info(f"[SCAN #{total_scans}] {len(candidates)} unique token candidates found")

    for token_address, profile in candidates.items():
        chain = profile.get("chainId", "")
        if chain not in ACTIVE_CHAINS:
            continue

        # Fetch pairs for this token
        pairs = fetch_token_pairs(token_address, chain)
        if not pairs:
            continue

        # Pick the pair with the most 24h volume
        pairs.sort(key=lambda p: (p.get("volume") or {}).get("h24") or 0, reverse=True)
        pair = pairs[0]

        pair_address = pair.get("pairAddress", "")
        if not pair_address or pair_address in seen_pairs:
            continue
        seen_pairs.add(pair_address)

        # ── Hard filters ────────────────────────
        liq     = (pair.get("liquidity") or {}).get("usd") or 0
        fdv     = pair.get("fdv") or 0
        vol_24h = (pair.get("volume") or {}).get("h24") or 0
        txns_h1 = (pair.get("txns") or {}).get("h1") or {}
        h1_total = txns_h1.get("buys", 0) + txns_h1.get("sells", 0)
        age_h   = fetch_pair_age_hours(pair)

        if liq < FILTERS["min_liquidity_usd"]:
            continue
        if liq > FILTERS["max_liquidity_usd"]:
            continue
        if 0 < fdv < FILTERS["min_fdv"]:
            continue
        if fdv > FILTERS["max_fdv"]:
            continue
        if vol_24h < FILTERS["min_volume_h24"]:
            continue
        if h1_total < FILTERS["min_txns_h1"]:
            continue
        if age_h > FILTERS["max_age_hours"]:
            continue

        # ── RugCheck (Solana) ────────────────────
        rugcheck = None
        if chain == "solana":
            rugcheck = fetch_rugcheck(token_address)
            if rugcheck:
                dangers = [r for r in (rugcheck.get("risks") or []) if r.get("level") == "danger"]
                if len(dangers) >= 3:
                    log.info(f"[SKIP] {token_address} — too many RugCheck dangers")
                    continue

        # ── Score it ────────────────────────────
        score, green, red = score_coin(pair, rugcheck)
        name   = (pair.get("baseToken") or {}).get("symbol", "???")
        log.info(f"[SCORED] {name} ({chain.upper()}) — {score}/100")

        if score < MIN_ALERT_SCORE:
            continue

        # ── Send alert ───────────────────────────
        msg = format_alert(pair, score, green, red)
        try:
            await app.bot.send_message(
                chat_id=CHAT_ID,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            alerts_this_scan  += 1
            total_alerts_sent += 1
            log.info(f"[ALERT SENT] {name} — score {score}")
            await asyncio.sleep(1.5)  # avoid Telegram rate limit
        except Exception as e:
            log.error(f"[SEND FAIL] {name}: {e}")

    log.info(f"[SCAN #{total_scans}] Done — {alerts_this_scan} alert(s) sent")
    return alerts_this_scan


# ─────────────────────────────────────────────
#  TELEGRAM COMMAND HANDLERS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
🤖 *Degen Gem Scanner Bot*
━━━━━━━━━━━━━━━━━━━━━━
Your 24/7 meme coin radar is LIVE 🔴

*What I do:*
• Scan DexScreener every 5 minutes
• Check new launches on Solana, Base, ETH, BSC
• Run RugCheck safety analysis (Solana)
• Score each coin 0–100
• Alert you only on real finds

*Commands:*
/start — This message
/scan — Force scan right now
/filters — Show current filter settings
/status — Bot uptime & stats
/help — Tips on reading alerts

_Score ≥ 75 = Strong play_
_Score ≥ 45 = Watch closely_
_Score < 45 = Skipped silently_
""".strip()
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 *Manual scan started...* give me a moment.", parse_mode="Markdown")
    count = await run_scan(context.application)
    await update.message.reply_text(
        f"✅ *Scan complete.* {count} alert(s) sent this round.",
        parse_mode="Markdown",
    )


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    f = FILTERS
    chains_str = ", ".join(c.upper() for c in ACTIVE_CHAINS)
    msg = f"""
⚙️ *Current Filter Settings*
━━━━━━━━━━━━━━━━━━━━━━
💧 Min Liquidity: `${f['min_liquidity_usd']:,}`
💧 Max Liquidity: `${f['max_liquidity_usd']:,}`
📊 Min FDV/MC: `${f['min_fdv']:,}`
📊 Max FDV/MC: `${f['max_fdv']:,}`
📦 Min 24h Volume: `${f['min_volume_h24']:,}`
🔢 Min 1h Txns: `{f['min_txns_h1']}`
⏱ Max Age: `{f['max_age_hours']}h`
🌐 Chains: `{chains_str}`
🎯 Min Score to Alert: `{MIN_ALERT_SCORE}/100`
⏰ Scan Interval: `every {SCAN_INTERVAL_SECONDS//60} min`
""".strip()
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now(timezone.utc) - bot_start_time
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    mins  = rem // 60
    msg = f"""
📡 *Bot Status*
━━━━━━━━━━━━━━━━━━━━━━
🟢 Status: RUNNING
⏱ Uptime: `{hours}h {mins}m`
🔁 Total Scans: `{total_scans}`
🚨 Total Alerts Sent: `{total_alerts_sent}`
👁 Pairs Already Seen: `{len(seen_pairs)}`
""".strip()
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
📖 *How to Read Gem Alerts*
━━━━━━━━━━━━━━━━━━━━━━
*Score guide:*
🔥🔥🔥 75–100 — Strong play, act fast
🔥🔥 60–74 — Solid, worth a small bag
🔥 45–59 — Watch it, wait for more buys

*Key metrics to watch in the alert:*
• *Liquidity* — lower = more volatile, higher = more stable
• *FDV/MC* — under $300k = early gem territory
• *1h Change* — 10–80% healthy; >100% may be too late
• *Buy pressure* — ideally >60% buys vs sells in 1h
• *RugCheck score* — above 800/1000 is clean on Solana

*BEFORE you buy:*
1. Click the DexScreener link in the alert
2. Check top holders manually (Solscan/Etherscan)
3. Confirm liquidity is locked
4. Check if there's a real Telegram/Twitter community
5. Only risk what you can lose 100%

⚠️ _This bot finds candidates. YOU decide to trade._
""".strip()
    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  SCHEDULED JOB
# ─────────────────────────────────────────────

async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    await run_scan(context.application)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main():
    if TELEGRAM_TOKEN == "PUT_YOUR_TOKEN_HERE" or CHAT_ID == "PUT_YOUR_CHAT_ID_HERE":
        print("❌ ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars first!")
        print("   export TELEGRAM_BOT_TOKEN='your_token'")
        print("   export TELEGRAM_CHAT_ID='your_chat_id'")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("scan",    cmd_scan))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("help",    cmd_help))

    # Auto-scan job
    app.job_queue.run_repeating(
        scheduled_scan,
        interval=SCAN_INTERVAL_SECONDS,
        first=15,   # first scan 15 seconds after bot starts
    )

    log.info("🤖 Degen Gem Scanner Bot is running...")
    log.info(f"   Scanning every {SCAN_INTERVAL_SECONDS // 60} minutes")
    log.info(f"   Watching chains: {', '.join(ACTIVE_CHAINS)}")
    log.info(f"   Alert threshold: score ≥ {MIN_ALERT_SCORE}/100")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
