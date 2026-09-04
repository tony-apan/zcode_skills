import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "model_inventory.py"
SECRET = "TOP_SECRET_MARKER_7f3a"


class ModelInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)

    def run_inventory(self, content=None, missing=False):
        config = self.temp / "config.json"
        if not missing:
            config.write_text(content, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_outputs_only_whitelisted_fields_and_never_secrets(self):
        config = {
            "provider": {
                "provider-safe-id": {
                    "name": "Provider {}".format(SECRET),
                    "enabled": True,
                    "options": {"apiKey": SECRET, "token": SECRET},
                    "baseURL": "https://{}.invalid".format(SECRET),
                    "unknown": SECRET,
                    "models": {
                        "model-safe-name": {
                            "limit": {"context": 200000, "output": SECRET},
                            "modalities": {"input": ["text", "image"], "output": [SECRET]},
                            "reasoning": {"variants": ["low", "high"], "secret": SECRET},
                            "Authorization": SECRET,
                        }
                    },
                }
            },
            "secret": SECRET,
        }
        result = self.run_inventory(json.dumps(config))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SECRET, result.stdout)
        self.assertNotIn("options", result.stdout)
        self.assertNotIn("baseURL", result.stdout)
        self.assertNotIn("Provider ", result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "providers": [
                    {
                        "enabled": True,
                        "id": "provider-safe-id",
                        "models": [
                            {
                                "limit": {"context": 200000},
                                "modalities": {"input": ["text", "image"]},
                                "name": "model-safe-name",
                                "reasoning": {"variants": ["low", "high"]},
                            }
                        ],
                    }
                ]
            },
        )

    def test_disabled_provider_is_reported(self):
        result = self.run_inventory(json.dumps({"provider": {"disabled-id": {"enabled": False, "models": {}}}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["providers"][0]["enabled"])

    def test_missing_enabled_defaults_to_true(self):
        result = self.run_inventory(json.dumps({"provider": {"default-id": {"models": {}}}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["providers"][0]["enabled"])

    def test_non_object_provider_and_model_entries_are_skipped(self):
        config = {
            "provider": {
                "bad-provider": SECRET,
                "good-provider": {
                    "models": {
                        "bad-model": SECRET,
                        "good-model": {"limit": {"context": "not-an-integer"}},
                    }
                },
            }
        }
        result = self.run_inventory(json.dumps(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([provider["id"] for provider in output["providers"]], ["good-provider"])
        self.assertEqual([model["name"] for model in output["providers"][0]["models"]], ["good-model"])
        self.assertIsNone(output["providers"][0]["models"][0]["limit"]["context"])
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_malformed_json_fails_without_leaking_body(self):
        result = self.run_inventory('{"provider": {"secret": "' + SECRET)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("malformed JSON", result.stderr)
        self.assertNotIn(SECRET, result.stderr)

    def test_missing_file_fails_without_path_contents(self):
        result = self.run_inventory(missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("file not found", result.stderr)
        self.assertNotIn(SECRET, result.stderr)


if __name__ == "__main__":
    unittest.main()
