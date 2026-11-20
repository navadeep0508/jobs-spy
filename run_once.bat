@echo off
:: Job Radar - run the pipeline once without Telegram alerts
:: Useful for testing scraping + scoring without sending notifications.
cd /d "%~dp0backend"
python main.py --no-notify
