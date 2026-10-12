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
| `MINT_COOLDOWN_SECONDS` | `300` by default; suppresses a second copy trigger for the same collection on the same chain for five minutes. `0` disables it |
| `WSS_<chainId>` | optional `wss://` endpoint (for example `WSS_8453`) for instant detection; see WebSocket mode |
| `WSS_BACKUP_SECONDS` | `5` by default; how often polling checks a chain whose socket is live |
| `TELEGRAM_BOT_TOKEN` | optional, from @BotFather |
| `TELEGRAM_CHAT_ID` | optional, the chat that receives alerts |
| `ALLOW_ESTIMATE_FALLBACK` | `false` by default, see Simulation below |
| `STATE_PATH` | `/data/state.json` |

3. Settings → add a Volume, mount path `/data`. That keeps the last processed block across restarts. Without it, a restart can miss mints that happened while it was down.
4. The start command is `python bot.py`. Redeploy.

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

## Telegram controls

With the same two variables set, type `/` in the chat with the bot. Telegram shows the command menu, so you do not have to type the names. The menu is registered when the bot starts. Redeploy after pulling this version, then open the chat and type `/`.

| Command | What it does |
|---|---|
| `/status` | Shows whether copying is on or paused, dry run, chains, and how many retries are waiting |
| `/off` | Pauses copying. The bot keeps watching blocks but skips new mints and tells you each one it skipped |
| `/on` | Resumes copying |
| `/wallets` | Lists the wallets being watched |
| `/watch 0xADDRESS` | Adds one or more wallets to watch, separated by spaces |
| `/unwatch 0xADDRESS` | Removes a wallet. The last wallet cannot be removed, so use `/off` to stop |
| `/minters` | Shows your minter and funder addresses, never their keys |
| `/balance` | Shows each minter's native balance on every chain |
| `/cap` | Shows the mint value cap per chain. `/cap 56 0.1` sets one chain and `/cap all 0.05` sets all |
| `/help` | Lists the commands |

- Wallets added or removed from Telegram are saved and then replace `WATCH_ADDRESSES`. Changing that variable later has no effect until the saved state is cleared. Your own minter and funder addresses cannot be added, and the list holds up to 50 wallets. A new wallet is watched from the next block, with no replay.
- `/cap` sets at most 1 native coin per chain. For a higher cap, change `MAX_MINT_ETH` or `MAX_MINT_<chainId>` in the host settings. A cap set from Telegram is saved and beats the variable.
- Saved settings (pause, wallets, caps) live in the state file, so they only survive a restart if `STATE_PATH` is on a volume. Without one, the bot goes back to the variables.
- Only messages from `TELEGRAM_CHAT_ID` are obeyed. Use a private chat with your bot, not a group, because anyone in a group chat could pause or resume it.
- Mints that happen while paused are not copied later, so there is no burst of late mints when you resume.
- The paused state is saved with the rest of the state. With a `/data` volume it survives restarts and redeploys.
- There is deliberately no command to turn dry run off. Change `DRY_RUN` in Railway for that.

To get a chat id, message your bot once, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.

## WebSocket mode

Set `WSS_<chainId>` to your provider's `wss://` URL (Alchemy gives one per chain) and the bot subscribes to mint logs for your watched wallets instead of waiting for the next poll. It reads each transaction's receipt, so a mint of 10 is copied as 10. Polling keeps running as a slow backup (`WSS_BACKUP_SECONDS`) while a socket is live and returns to full speed if the socket drops. A transaction seen by both is copied once. The bot reconnects with backoff, resubscribes when you change the watch list, and sends one Telegram alert after five failed connects in a row. Look for `<chain> websocket live` in the logs, or `websocket live:` in `/status`. Chains without a `WSS_` variable just poll.

## Simulation

Every copy is simulated first, using `eth_simulateV1` or `debug_traceCall`. Public RPCs often support neither. When the RPC cannot simulate, the bot skips the mint and logs which `RPC_<chainId>` to replace. Setting `ALLOW_ESTIMATE_FALLBACK=true` lets it send on a gas estimate alone, which does not prove that the minter receives the NFT. Leave it off unless you accept that.

## Tests

```bash
python -m unittest discover -s tests
```

No network or keys are needed. The tests cover call decoding, mint detection, fee-bump tracking, caps, state saving and alerts.

## Rules

- `WATCH_ADDRESSES` must be other people. Do not put your own minter keys in that list, or a copy could loop.
- Cross-wallet duplicate suppression is shared across all watched wallets and keyed by chain plus NFT collection. The first eligible mint claims the cooldown and other transactions for that collection are skipped until it expires, including repeat mints by the same wallet. If a copy finishes with no minter minting and is not retrying, the claim is released. Set `MINT_COOLDOWN_SECONDS=0` to disable it. A source transaction touching several collections is suppressed as a whole if any of them is cooling down.
- `MAX_MINT_ETH` skips a mint whose transaction value is above the cap. The number is in each chain's own coin (ETH, BNB, POL), so use `MAX_MINT_<chainId>` where 0.05 is the wrong size. Raise it if you want to copy paid mints.
- Minter wallets are hot wallets. Keep only what a few mints need in them, and refill from the funder.
- A copied mint can still be a token you cannot sell. The simulation proves you receive the NFT, not that it has value.
- `DRY_RUN=true` logs the transaction and does not broadcast. Each minter that would have minted is reported on Telegram as "would mint", and nothing is sent or bridged.
- Bridging is not instant. A mint can sell out while funds are in flight. `BRIDGE_TIMEOUT` defaults to 180 seconds.
- Each minter sends the full copied transaction, so four minters mint the same quantity each, not split across them.
