@echo off
:: Job Radar - start the full pipeline (scrape -> score -> notify -> export)
:: Telegram alerts are sent if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env / environment.
cd /d "%~dp0backend"
python main.py
