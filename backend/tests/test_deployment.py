from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DeploymentTests(unittest.TestCase):
    def test_kismat_workflow_exists_and_has_three_hour_cron(self) -> None:
        workflow = ROOT / ".github" / "workflows" / "kismat-scheduled.yml"
        self.assertTrue(workflow.is_file())
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("17 */3 * * *", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("jobs-db-backup-", text)
        self.assertIn("concurrency:", text)
        self.assertIn("timeout-minutes: 45", text)

    def test_old_two_hour_scheduler_removed(self) -> None:
        self.assertFalse(
            (ROOT / ".github" / "workflows" / "scrape.yml").exists()
        )

    def test_env_example_has_required_names(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for name in (
            "NVIDIA_API_KEY",
            "NVIDIA_BASE_URL",
            "NVIDIA_MODEL",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ):
            self.assertIn(name + "=", text)

    def test_database_and_secret_ignore_rules(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for rule in (".env", "data/*.db", "data/*.db-wal", "backups/", "venv/", ".venv/"):
            self.assertIn(rule, text)


if __name__ == "__main__":
    unittest.main()
