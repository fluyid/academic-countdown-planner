"""Unit tests for onboarding setup wizard & environment check (`cli.py setup`)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.gemini.client import REPO_ROOT
from src.setup_wizard import (
    collect_setup_checks,
    format_setup_checks,
    install_credentials_file,
    run_setup_wizard,
    save_gemini_api_key,
    update_user_config,
)


class TestSetupWizard(unittest.TestCase):
    def test_install_credentials_and_save_gemini_key_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sec = base / "secrets"

            # Reject repo-internal destination for secrets.
            src_creds = base / "downloaded.json"
            src_creds.write_text(json.dumps({"installed": {"client_id": "abc"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                install_credentials_file(src_creds, base_secrets_dir=REPO_ROOT / ".secrets")
            with self.assertRaises(ValueError):
                save_gemini_api_key("my-key", base_secrets_dir=REPO_ROOT / ".secrets")

            # Reject non-OAuth JSON.
            bad_json = base / "bad.json"
            bad_json.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Desktop App JSON"):
                install_credentials_file(bad_json, base_secrets_dir=sec)

            # Valid OAuth JSON copies cleanly into outside-repo secrets dir.
            dest = install_credentials_file(src_creds, base_secrets_dir=sec)
            self.assertEqual(dest, sec / "credentials.json")
            self.assertTrue(dest.is_file())

            # Saving GEMINI_API_KEY preserves unrelated lines and updates existing key cleanly.
            env_file = sec / ".env"
            env_file.write_text("# comment\nOTHER=1\nGEMINI_API_KEY=old\n", encoding="utf-8")
            save_gemini_api_key("new-secret-key", base_secrets_dir=sec)
            content = env_file.read_text(encoding="utf-8")
            self.assertIn("OTHER=1", content)
            self.assertIn("GEMINI_API_KEY=new-secret-key", content)
            self.assertNotIn("GEMINI_API_KEY=old", content)

    def test_interactive_setup_wizard_and_check_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sec = base / "secrets"
            cfg = base / "targets.json"
            cfg.write_text(
                json.dumps(
                    {
                        "calendars": {"personal": "Kai Nath", "university": "IU"},
                        "tasks": {"list_name": "Countdown Planner (Test)"},
                        "targets": [],
                    }
                ),
                encoding="utf-8",
            )
            dl_creds = base / "client_secret.json"
            dl_creds.write_text(json.dumps({"installed": {"client_id": "cid"}}), encoding="utf-8")

            # --check fails (exit 1) before credentials.json exists.
            out_before: list[str] = []
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
                rc_before = run_setup_wizard(
                    check_only=True,
                    config_path=cfg,
                    base_secrets_dir=sec,
                    print_fn=out_before.append,
                    browser_finder=lambda: Path("/fake/chrome"),
                )
            self.assertEqual(rc_before, 1)
            self.assertIn("[MISSING]", "\n".join(out_before))

            # Run interactive wizard: provide creds path, Gemini key, and custom calendar/tasks names.
            answers = iter([str(dl_creds), "Alice", "MIT", "Study Plan"])
            out_run: list[str] = []
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
                rc_run = run_setup_wizard(
                    check_only=False,
                    config_path=cfg,
                    base_secrets_dir=sec,
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "ai-studio-key-123",
                    print_fn=out_run.append,
                    browser_finder=lambda: Path("/fake/chrome"),
                )
            self.assertEqual(rc_run, 0)
            self.assertTrue((sec / "credentials.json").is_file())
            self.assertIn("GEMINI_API_KEY=ai-studio-key-123", (sec / ".env").read_text(encoding="utf-8"))
            self.assertNotIn("ai-studio-key-123", "\n".join(out_run))  # Never echo API key to stdout.

            updated = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(updated["calendars"], {"personal": "Alice", "university": "MIT"})
            self.assertEqual(updated["tasks"]["list_name"], "Study Plan")

            # --check now passes (exit 0).
            checks = collect_setup_checks(
                config_path=cfg,
                base_secrets_dir=sec,
                browser_finder=lambda: Path("/fake/chrome"),
            )
            rendered = format_setup_checks(checks)
            self.assertNotIn("[MISSING]", rendered)
            self.assertIn("Alice", rendered)

            # update_user_config leaves unspecified fields untouched.
            only_tasks = update_user_config(cfg, tasks_list_name="My Tasks")
            self.assertEqual(only_tasks["calendars"]["personal"], "Alice")
            self.assertEqual(only_tasks["tasks"]["list_name"], "My Tasks")


if __name__ == "__main__":
    unittest.main()
