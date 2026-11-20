# GitHub Actions deployment

## Repository setup

1. Create the GitHub repository `navadeep0508/jobs-spy`.
2. Push this project with `main` as the default branch.
3. In **Settings > Actions > General**, allow Actions and enable the workflow permissions required by the repository.
4. In **Settings > Secrets and variables > Actions**, add:

| Secret | Purpose |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA NIM authentication |
| `NVIDIA_BASE_URL` | NVIDIA OpenAI-compatible base URL; default is `https://integrate.api.nvidia.com/v1` |
| `NVIDIA_MODEL` | Exact model ID visible in the NVIDIA account catalog |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Authorized destination chat ID |

Optional source credentials: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_KEY`, `FINDWORK_TOKEN`, and `MUSE_API_KEY`.

Never place credentials in remote URLs or committed files. Jobs Spy never invents or silently substitutes a NVIDIA model ID.

## Schedule

[kismat-scheduled.yml](.github/workflows/kismat-scheduled.yml) runs one scrape cycle at `17 */3 * * *` UTC: 00:17, 03:17, 06:17, 09:17, 12:17, 15:17, 18:17, and 21:17. GitHub may delay scheduled runs during busy periods.

Run it manually from **Actions > Jobs Spy three-hour cycle > Run workflow**. Manual runs use the same database restoration and notification deduplication path.

## SQLite persistence

The application database is `data/jobs.db`. WAL mode, busy timeout, foreign keys, migrations, transactions, and integrity checks are enabled.

At the start, the workflow finds the latest non-expired `jobs-db-backup-*` GitHub Actions artifact, downloads it, verifies integrity, and restores it safely. Missing or corrupted backups are handled without deleting the last known-good artifact.

At the end, the workflow uses SQLite's backup API, runs `PRAGMA integrity_check`, and uploads a 30-day artifact named `jobs-db-backup-<run>-<attempt>`. Artifacts are temporary, not permanent storage.

Local backup and restore:

```bash
cd backend
python ../scripts/db_backup.py backup --source ../data/jobs.db --destination ../backups/jobs.db
python ../scripts/db_backup.py restore --backup ../backups/jobs.db --target ../data/jobs.db
python ../scripts/db_backup.py check --database ../data/jobs.db
```

## NVIDIA and Telegram

NVIDIA NIM is the only hosted AI provider. Missing credentials or API/JSON failures use a deterministic fallback labeled `fallback`, never as NVIDIA AI evaluation.

The Telegram bot only alerts the authorized chat. Only successful delivery is marked as sent, and notification history is stored in SQLite.

## Troubleshooting

- **No artifact:** the first run initializes or migrates the local SQLite database and uploads a valid backup.
- **Corrupted artifact:** restore is rejected and the known-good artifact is retained.
- **Expired artifact:** restore an externally retained backup or accept a fresh database initialization.
- **Missing NVIDIA model:** configure an exact model ID visible to the account.
- **Telegram unavailable:** scraping continues and later runs may retry notification.
- **Scheduled delay:** GitHub-hosted runners may start scheduled workflows late.

## Limitations

GitHub Actions provides scheduled execution but not a persistent web server or permanent artifact storage. Use a separate static host for the dashboard. SQLite remains the only application database. Never publish secrets or database backups publicly.
