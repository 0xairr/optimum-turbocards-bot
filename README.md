# Optimum TurboCards Bot

Watches [@get_optimum](https://x.com/get_optimum) on X for TurboCard access-code drops, claims a code on the
[TurboCards mint site](https://turbocards.getoptimum.xyz/optimum) the moment one is posted, and mints the
soulbound card on Ethereum mainnet. No X API keys and no browser needed.

On Drop #1 (20 codes, first come first served) it claimed a code about 2 seconds after the post went up and
minted a card.

> **Read first**
> - Use a **fresh wallet** made just for this. The bot creates one for you. Never put a main-wallet key on a VPS.
> - TurboCards are **soulbound**: the card stays in the bot's wallet forever, so back that wallet up.
> - First come, first served. Other people run bots too, so there is no guarantee you win a code.
> - Not affiliated with Optimum or Snag. Use at your own risk.

## How it works

1. **Watch.** Two staggered pollers read @get_optimum's timeline through the free
   [FxTwitter](https://github.com/FxEmbed/FxEmbed) API, about every 0.6 s.
2. **Spot codes.** A new post (under 10 minutes old) is scanned for code-like tokens such as `RYV20H9Nzc5P`,
   `ixcKpRTDJZWG` or `J-SHWlW_9VUL`. Links, times, @handles and ordinary words are skipped.
3. **Claim.** Codes are tried **one at a time** through the Snag API, signed in with your wallet (Sign-In with
   Ethereum). It starts 75% of the way down the list, where fewer people are racing, and stops at the first code
   that works. Snag locks claiming for a minute after too many fast attempts, so the bot never bursts.
4. **Mint.** It asks Snag for a mint signature, checks it is free and mints to your own wallet, then sends
   `mintWithSignature` to several public RPCs at once and waits for the receipt.

After it mints, the bot keeps running but does nothing more. It claims one card per wallet.

## What you need

- Python 3.10 or newer
- About **0.003 ETH** on Ethereum mainnet in the bot's wallet for gas. The mint itself is free and costs much
  less than that, but the bot checks it can cover a gas spike before sending.
- A machine that stays on and online when the drop happens: your PC, or a small VPS.

## Setup on your PC (Windows, macOS, Linux)

```bash
git clone https://github.com/0xairr/optimum-turbocards-bot.git
cd optimum-turbocards-bot
python -m venv .venv
```

Then use the venv's Python. On **Windows** it is `.venv\Scripts\python`, on **macOS/Linux** `.venv/bin/python`:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python optimum_bot.py --selftest
```

The self-test creates `wallet.json` (your new wallet), signs in to the mint site, submits the one-time
"connect wallet" and "follow" tasks (codes claim fine even if "follow" never shows as done), and checks the X
feed and RPCs. It prints the wallet address.
**Send about 0.003 ETH (mainnet) to that address**, then start the bot:

```bash
.venv/bin/python optimum_bot.py
```

Leave it running. Make sure the computer won't sleep: on Windows set *Settings → System → Power → Sleep* to
*Never*; on macOS start the bot with `caffeinate -i .venv/bin/python optimum_bot.py`.

## Setup on a VPS (Ubuntu + systemd)

Any small Linux VPS works (1 vCPU / 512 MB is plenty).

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/0xairr/optimum-turbocards-bot.git ~/optimum-watch
cd ~/optimum-watch
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python optimum_bot.py --selftest      # prints your wallet address - fund it
```

Install it as a service. The unit restarts the bot if it crashes and starts it on boot. The `sed` adapts the
paths and user to your machine:

```bash
sed -e "s#/home/ubuntu/optimum-watch#$PWD#g" -e "s#/home/ubuntu#$HOME#g" \
    -e "s#^User=ubuntu#User=$USER#" -e "s#^Group=ubuntu#Group=$(id -gn)#" \
    deploy/optimum-watch.service | sudo tee /etc/systemd/system/optimum-watch.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now optimum-watch
journalctl -u optimum-watch -f      # live log
```

Stop it for good with `sudo systemctl disable --now optimum-watch`.

## Back up the wallet

`wallet.json` holds the private key of the wallet your card lives in. Copy the `private_key` value into a
password manager, and import it into MetaMask or Rabby (*Import account → Private key*) to see the card.

Want the card in a wallet you already have? Before the first run, create `wallet.json` yourself with
`{"private_key": "0x..."}`. Only do this with a wallet that holds nothing else, because the key sits in a
plain file on the machine.

## Files the bot writes

| File | What it is |
|---|---|
| `wallet.json` | Bot wallet private key. Keep it secret; never commit it (it is git-ignored). |
| `state.json` | Progress: setup tasks done, code claimed, card minted. |
| `events.jsonl` | Full log, one JSON line per event. Search for `CODE_CLAIMED`, `MINTED` or `FUND_WALLET`. |
| `status.json` | Health snapshot written every minute: last feed read, session, wallet balance. |

## Limitations

- Only reads codes written as **text** in posts from @get_optimum. Codes in images or posted only on Discord are
  missed.
- FxTwitter is a free third-party service. It can lag or return errors, and the bot keeps retrying.
- An ALL-CAPS word in a fresh post or retweet (like `PAUSE`) is tried as a last-resort code. Each wasted attempt
  counts toward Snag's one-minute lock.
- `--selftest` submits one fake code to check the claim endpoint, so don't run it in the minutes before a drop.
- The Snag site, rule and contract IDs at the top of `optimum_bot.py` are specific to the TurboCards site. If
  Optimum changes the mint page, they need updating.

## Tests

```bash
.venv/bin/python -m unittest -v
```

The tests cover code extraction (including the real Drop #1 post), claim order and pacing, setup tasks, and
byte-for-byte mint calldata against a real mainnet mint.

## License

MIT
