# EcoFlow Charging Monitor

A Telegram-first app for checking the charging status of an EcoFlow power station. The bot keeps a live status dashboard in your chat and sends alerts when the station's status changes.

> **Current interface:** Telegram bot only. There is no web dashboard or mobile app.

## What it does

- Shows battery charge, input and output power, grid, solar and USB power, estimated remaining time, and online status.
- Refreshes the dashboard automatically (every 15 seconds by default), or on demand.
- Alerts when the station goes online or offline, charging status changes, grid power changes, or the battery crosses the configured low-charge threshold.
- Supports connecting a station in a private chat, or using the server-configured EcoFlow account in a group.

The dashboard's **Restart** button refreshes the dashboard message; it does not restart the power station or change its charging settings.

## Requirements

- Python 3.12+ (or Docker with Docker Compose)
- An EcoFlow Open API access key and secret key
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Set up

1. Clone the repository and enter its directory.
2. Create a `.env` file using the example:

   ```bash
   cp .env.example .env
   ```

3. Fill in the EcoFlow and Telegram settings:

   | Variable | Required | Description |
   | --- | --- | --- |
   | `ECOFLOW_ACCESS_KEY` | For server-configured chats | EcoFlow Open API access key |
   | `ECOFLOW_SECRET_KEY` | For server-configured chats | EcoFlow Open API secret key |
   | `ECOFLOW_SN` | For server-configured chats | Serial number of the station to monitor |
   | `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
   | `TELEGRAM_CHAT_ID` | No | Optional chat ID for the initial server-configured chat |
   | `ECOFLOW_LOW_SOC` | No | Low-charge alert threshold; defaults to `20` percent |
   | `ECOFLOW_POLL_SECONDS` | No | Polling interval; defaults to `15` seconds |

   A private chat can instead connect its own EcoFlow account and choose a station using `/start`.

4. Install dependencies and start the bot:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python bot.py
   ```

Alternatively, start it with Docker Compose:

```bash
docker compose up -d --build
```

## Use the bot

- `/start` — connect a station or refresh the existing dashboard.
- `/status` — request an immediate status update.
- `/stop` — stop updates in this chat and remove its saved connection.
- `/cancel` — cancel the private-chat setup flow.

The dashboard also includes buttons for refreshing, reposting, and stopping it. Add the bot to a group to use the EcoFlow account configured in the server's `.env`.

## Credential handling

Private-chat setup asks you to send your EcoFlow API keys to the bot. The bot deletes the key messages after receiving them and stores the connection on the machine running the bot in `.telegram_chats.json`, with owner-only file permissions. Keep the host and its files private, and never commit `.env` or `.telegram_chats.json`.

## Deployment

`docker-compose.yml` runs the bot as a restartable container. The included `deploy.sh` copies the project to the configured SSH server and rebuilds the container there. Edit the server address in that script before using it, and make sure your SSH account can run Docker Compose with `sudo`.
