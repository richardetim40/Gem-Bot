 🤖 Gem-Bot — Solana Memecoin Scanner

> A Telegram bot that monitors Solana memecoin markets in real time, detecting pre-pump signals and tracking smart wallets automatically.

What it does

Gem-Bot scans Solana on-chain activity via the Helius API and sends real-time alerts to a Telegram channel or group when it detects early signs of token momentum — before the pump happens.
 Core features

- Pre-pump detection — filters transactions by volume thresholds and new token activity to flag early momentum
- Auto wallet discovery — identifies and tracks wallets associated with suspicious or high-activity token buys
- Custom wallet tracking — add specific wallets to monitor via Telegram commands
- Helius WebSocket integration — real-time account update streaming, no polling
- Telegram alert system — instant notifications with token mint address and signal details


Tech stack

| Layer | Technology |
| Language | Python |
| Blockchain | Solana |
| Data source | Helius API (WebSocket + REST) |
| Bot interface | Telegram Bot API |
| Deployment | JustRunMy.App |

---

## Project structure

```
Gem-Bot/
├── gem_scanner_bot.py   # Main bot — scanner logic + Telegram integration
├── requirements.txt     # Python dependencies
└── Proclife.txt         # Process/deployment config
```

---

## How it works

```
Helius WebSocket stream
        │
        ▼
 Account update received
        │
        ▼
 Filter: new token? volume > threshold?
        │
        ├── Yes → Extract mint address → Send Telegram alert 🚨
        │
        └── No  → Discard
```

---

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/richardetim40/Gem-Bot.git
cd Gem-Bot
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set environment variables
```bash
HELIUS_API_KEY=your_helius_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_or_channel_id
```

### 4. Run
```bash
python gem_scanner_bot.py
```

---

## Version history

| Version | What changed |
|---|---|
| v1.0 | Basic scanner, manual wallet input |
| v2.0 | Added WebSocket streaming via Helius |
| v3.0 | Auto wallet discovery |
| v4.0 | Pre-pump detection logic |
| v4.1 | Custom wallet tracking commands, stability fixes |

---

## Built by

**Richard Etim** — AI-directed developer & prompt engineer based in Uyo, Nigeria.  
Built using Claude AI + prompt engineering.

[Portfolio](https://github.com/richardetim40) · [LinkedIn](https://www.linkedin.com/in/richard-akpaessien-774892419?utm_source=share_via&utm_content=profile&utm_medium=member_android)

