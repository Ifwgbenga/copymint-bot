# Copymint bot

Watches one or more wallets. When one of them mints an NFT to itself, your wallets send that same mint for the same quantity.

A copy is taken from the mint call only, including a mint buried in a multicall. Signature and allowlist calls are skipped. The bot simulates the copy first and broadcasts only when that simulation mints the same collection and quantity. A send that does not land is retried. If it is still pending after two blocks, it is replaced once with a higher fee.

If a minter does not have enough native coin on that chain, the bot swaps or bridges funds in with [LI.FI](https://li.quest) from another chain, then mints. A separate funder wallet can pay for the minters.

It starts at the current block. It does not replay old mints.

Signed allowlist mints cannot be copied. The signature is locked to the watched wallet, the copied call reverts, and that wallet is skipped.

## Chains

Ethereum, Optimism, BNB Chain, Polygon, Base, Arbitrum, HyperEVM, and Robinhood. Set `CHAINS` to the ones you want.

## GitHub

From this folder:

```bash
git init
git add .
git commit -m "copy mint bot"
```

Create a new empty repository on GitHub, then:

```bash
git remote add origin git@github.com:YOUR_USER/copymint-bot.git
git branch -M main
git push -u origin main
```

Do not commit a `.env` file. Keys belong in Railway variables only.

## Railway

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → select this repo.
2. Open the service → Variables, and add:

| Variable | Example |
|---|---|
| `WATCH_ADDRESSES` | `0xabc...,0xdef...` |
| `MINTER_PRIVATE_KEYS` | `0xkey1,0xkey2` |
| `FUNDER_PRIVATE_KEY` | optional, the wallet that holds funds on other chains |
| `CHAINS` | `1,10,56,137,8453,42161,999,4663` |
| `RPC_1` | your Ethereum RPC |
| `RPC_4663` | your Robinhood RPC |
| `RPC_8453` | Base, and the same for any chain you leave unset only if you accept the public RPC |
| `ENABLE_BRIDGE` | `true` |
| `MAX_MINT_ETH` | `0.05`, the cap in each chain's native coin |
| `MAX_MINT_<chainId>` | optional per-chain cap, for example `MAX_MINT_56=0.1` for BNB Chain |
| `TELEGRAM_BOT_TOKEN` | optional, from @BotFather |
| `TELEGRAM_CHAT_ID` | optional, the chat that receives alerts |
| `ALLOW_ESTIMATE_FALLBACK` | `false` by default, see Simulation below |
| `DRY_RUN` | `true` while testing. Delete it or set `false` to broadcast |
| `STATE_PATH` | `/data/state.json` |

3. Settings → add a Volume, mount path `/data`. That keeps the last processed block across restarts. Without it, a restart can miss mints that happened while it was down.
4. The start command is `python bot.py`. Redeploy.

## Dry run

On Railway: add `DRY_RUN` = `true` under Variables, then redeploy. Startup logs `dry on`. A watched mint is simulated and logged, and nothing is broadcast or bridged. When you're ready to go live, change it to `false` or delete it, then redeploy. Startup should log `dry off`.

Public RPCs are filled in for any `RPC_<chainId>` you leave empty. They rate-limit. Set Alchemy or an equivalent URL for any chain you really care about.

Watch the Deploy logs. A healthy start looks like:

```
watching 1 wallet(s) on Ethereum, Robinhood
minters 0x..., 0x...
bridge on dry off
Robinhood watching from 84123456
```

When a watched wallet mints, the log names the chain, the contract, and each copy:

```
mint by 0xabc... on Base -> 0xcontract value 0.000000 0xhash
funding 0x0381... from BNB Chain native
bridge OK 0xhash
OK 0x0381... 0xhash
```

## Telegram alerts

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` and the bot messages you when it starts, when a watched wallet mints, when a copy lands, and when a copy is given up on, skipped for lack of funds, or a bridge times out. Leave them unset and the bot logs only. Alerts are sent in the background, so a slow Telegram never delays a mint.

To get a chat id, message your bot once, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.

## Simulation

Every copy is simulated first, using `eth_simulateV1` or `debug_traceCall`. Public RPCs often support neither. When the RPC cannot simulate, the bot skips the mint and logs which `RPC_<chainId>` to replace. Setting `ALLOW_ESTIMATE_FALLBACK=true` lets it send on a gas estimate alone, which does not prove that the minter receives the NFT. Leave it off unless you accept that.

## Tests

```bash
python -m unittest discover -s tests
```

No network or keys are needed. The tests cover call decoding, mint detection, fee-bump tracking, caps, state saving and alerts.

## Rules

- `WATCH_ADDRESSES` must be other people. Do not put your own minter keys in that list, or a copy could loop.
- `MAX_MINT_ETH` skips a mint whose transaction value is above the cap. The number is in each chain's own coin (ETH, BNB, POL), so use `MAX_MINT_<chainId>` where 0.05 is the wrong size. Raise it if you want to copy paid mints.
- Minter wallets are hot wallets. Keep only what a few mints need in them, and refill from the funder.
- A copied mint can still be a token you cannot sell. The simulation proves you receive the NFT, not that it has value.
- `DRY_RUN=true` logs the transaction and does not broadcast.
- Bridging is not instant. A mint can sell out while funds are in flight. `BRIDGE_TIMEOUT` defaults to 180 seconds.
- Each minter sends the full copied transaction, so four minters mint the same quantity each, not split across them.
