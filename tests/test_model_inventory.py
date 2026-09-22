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

    def run_inventory(self, content=None, missing=False, provider_content=None, provider_missing=True, explicit_provider=False):
        config = self.temp / "config.json"
        provider_config = self.temp / "provider_config.json"
        if not missing:
            config.write_text(content, encoding="utf-8")
        if not provider_missing:
            provider_config.write_text(provider_content, encoding="utf-8")
        command = [sys.executable, str(SCRIPT), "--config", str(config)]
        if explicit_provider:
            command.extend(["--provider-config", str(provider_config)])
        return subprocess.run(
            command,
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
                "generator": "tony-agents-pack/model_inventory",
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
                ],
                "schema_version": 1,
                "verification": "DECLARED_UNVERIFIED",
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

    def test_falls_back_to_provider_config_when_config_has_no_models(self):
        provider_config = {
            "schemaVersion": 1,
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {
                            "providerId": "provider-safe-id",
                            "providerName": "Provider {}".format(SECRET),
                            "config": {
                                "access": {"type": "api-key", "apiKey": SECRET},
                                "api": {"baseURL": "https://{}.invalid".format(SECRET)},
                                "personalModelIds": ["model-b", "model-a", "model-a"],
                                "modelOrder": ["model-a", "model-b"],
                            },
                        }
                    ]
                },
                "modelConfigRules": {
                    "providerModelRules": [
                        {
                            "providerId": "provider-safe-id",
                            "modelId": "model-a",
                            "config": {
                                "properties": {"contextWindow": 1000000, "unknown": SECRET},
                                "optionSpecs": {"reasoningLevel": {"values": ["low", "high"]}},
                            },
                        },
                        {
                            "providerId": "provider-safe-id",
                            "modelId": "model-b",
                            "config": {"properties": {"contextWindow": 200000}},
                        },
                    ],
                    "manualProviderModelRules": [],
                },
            },
        }
        result = self.run_inventory(
            json.dumps({"provider": {}}),
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn("providerName", result.stdout)
        self.assertNotIn("access", result.stdout)
        self.assertNotIn("baseURL", result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "generator": "tony-agents-pack/model_inventory",
                "providers": [
                    {
                        "enabled": True,
                        "id": "provider-safe-id",
                        "models": [
                            {
                                "limit": {"context": 1000000},
                                "modalities": {"input": []},
                                "name": "model-a",
                                "reasoning": {"variants": ["low", "high"]},
                            },
                            {
                                "limit": {"context": 200000},
                                "modalities": {"input": []},
                                "name": "model-b",
                                "reasoning": {"variants": []},
                            },
                        ],
                    }
                ],
                "schema_version": 1,
                "verification": "DECLARED_UNVERIFIED",
            },
        )

    def test_falls_back_when_legacy_config_file_is_missing(self):
        provider_config = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {"providerId": "provider-safe-id", "config": {"personalModelIds": ["model-a"]}}
                    ]
                },
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            }
        }
        result = self.run_inventory(
            missing=True,
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["providers"][0]["models"][0]["name"], "model-a")

    def test_malformed_provider_config_fails_even_when_legacy_has_models(self):
        result = self.run_inventory(
            json.dumps({"provider": {"provider-id": {"models": {"model-a": {}}}}}),
            provider_content='{"broken":',
            provider_missing=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("provider configuration file contains malformed JSON", result.stderr)

    def test_missing_optional_provider_config_preserves_empty_legacy_inventory(self):
        result = self.run_inventory(json.dumps({"provider": {}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "generator": "tony-agents-pack/model_inventory",
                "providers": [],
                "schema_version": 1,
                "verification": "DECLARED_UNVERIFIED",
            },
        )

    def test_provider_config_completes_inventory_and_filters_stale_legacy_models(self):
        legacy = {
            "provider": {
                "provider-a": {
                    "models": {
                        "active-model": {
                            "limit": {"context": 120000},
                            "modalities": {"input": ["text", "image"]},
                            "reasoning": {"variants": ["high"]},
                        },
                        "stale-model": {},
                    }
                }
            }
        }
        provider_config = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {"providerId": "provider-a", "config": {"personalModelIds": ["active-model"]}},
                        {"providerId": "provider-b", "config": {"personalModelIds": ["other-model"]}},
                    ]
                },
                "modelConfigRules": {
                    "providerModelRules": [
                        {
                            "providerId": "provider-a",
                            "modelId": "active-model",
                            "config": {"properties": {"contextWindow": 100000}},
                        },
                        {
                            "providerId": "provider-b",
                            "modelId": "other-model",
                            "config": {"properties": {"contextWindow": 200000}},
                        },
                    ],
                    "manualProviderModelRules": [],
                },
            }
        }
        result = self.run_inventory(
            json.dumps(legacy),
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([provider["id"] for provider in output["providers"]], ["provider-a", "provider-b"])
        provider_a = output["providers"][0]
        self.assertEqual([model["name"] for model in provider_a["models"]], ["active-model"])
        self.assertEqual(provider_a["models"][0]["limit"]["context"], 120000)
        self.assertEqual(provider_a["models"][0]["modalities"]["input"], ["text", "image"])
        self.assertEqual(provider_a["models"][0]["reasoning"]["variants"], ["high"])

    def test_provider_config_disabled_and_explicit_empty_models_are_authoritative(self):
        legacy = {
            "provider": {
                "disabled-provider": {"enabled": True, "models": {"stale": {}}},
                "empty-provider": {"enabled": True, "models": {"stale": {}}},
            }
        }
        provider_config = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {
                            "providerId": "disabled-provider",
                            "config": {"enabled": False, "personalModelIds": ["active"]},
                        },
                        {
                            "providerId": "empty-provider",
                            "config": {"enabled": True, "personalModelIds": [], "modelOrder": []},
                        },
                    ]
                },
                "modelConfigRules": {
                    "providerModelRules": [
                        {
                            "providerId": "disabled-provider",
                            "modelId": "active",
                            "config": {"properties": {"contextWindow": 100000}},
                        }
                    ],
                    "manualProviderModelRules": [],
                },
            }
        }
        result = self.run_inventory(
            json.dumps(legacy),
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        providers = {provider["id"]: provider for provider in json.loads(result.stdout)["providers"]}
        self.assertFalse(providers["disabled-provider"]["enabled"])
        self.assertEqual([model["name"] for model in providers["disabled-provider"]["models"]], ["active"])
        self.assertEqual(providers["empty-provider"]["models"], [])

    def test_provider_config_missing_authority_fields_preserves_legacy_values(self):
        legacy = {"provider": {"provider-a": {"enabled": False, "models": {"legacy-model": {}}}}}
        provider_config = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [{"providerId": "provider-a", "config": {"access": {}}}]
                },
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            }
        }
        result = self.run_inventory(
            json.dumps(legacy),
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        provider = json.loads(result.stdout)["providers"][0]
        self.assertFalse(provider["enabled"])
        self.assertEqual([model["name"] for model in provider["models"]], ["legacy-model"])

    def test_provider_model_list_type_error_fails_closed(self):
        provider_config = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {"providerId": "provider-a", "config": {"personalModelIds": "model-a"}}
                    ]
                },
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            }
        }
        result = self.run_inventory(
            json.dumps({"provider": {"provider-a": {"models": {"legacy": {}}}}}),
            provider_content=json.dumps(provider_config),
            provider_missing=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a JSON array", result.stderr)

    def test_utf8_bom_and_utf16_json_are_supported_with_or_without_bom(self):
        provider_value = {
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {"providerId": "provider-a", "config": {"personalModelIds": ["model-a"]}}
                    ]
                },
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
            }
        }
        config_value = {"provider": {}}
        config = self.temp / "config.json"
        provider_config = self.temp / "provider_config.json"
        encodings = {
            "utf-8-sig": lambda text: text.encode("utf-8-sig"),
            "utf-16": lambda text: text.encode("utf-16"),
            "utf-16-le-no-bom": lambda text: text.encode("utf-16-le"),
            "utf-16-be-no-bom": lambda text: text.encode("utf-16-be"),
        }
        for config_name, encode_config in encodings.items():
            for provider_name, encode_provider in encodings.items():
                with self.subTest(config=config_name, provider_config=provider_name):
                    config.write_bytes(encode_config(json.dumps(config_value)))
                    provider_config.write_bytes(encode_provider(json.dumps(provider_value)))
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--config", str(config)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["providers"][0]["models"][0]["name"], "model-a")

    def test_non_utf8_config_fails_cleanly(self):
        config = self.temp / "config.json"
        config.write_bytes(b"\xff\xfe")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertTrue(
            "not valid UTF-8 or UTF-16" in result.stderr or "contains malformed JSON" in result.stderr
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_sanitized_zcode_3_12_3_fixture_is_supported(self):
        fixture = ROOT / "tests" / "fixtures" / "provider_config.v3.12.3.sanitized.json"
        config = self.temp / "config.json"
        config.write_text(json.dumps({"provider": {}}), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config",
                str(config),
                "--provider-config",
                str(fixture),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        provider = output["providers"][0]
        self.assertEqual(provider["id"], "provider-example")
        self.assertEqual(provider["models"][0]["name"], "model-example")
        self.assertEqual(provider["models"][0]["limit"]["context"], 1000000)
        self.assertEqual(provider["models"][0]["reasoning"]["variants"], ["low", "high"])
        self.assertNotIn("<redacted>", result.stdout)
        self.assertNotIn("providerName", result.stdout)
        self.assertNotIn("apiKey", result.stdout)
        self.assertNotIn("baseURL", result.stdout)

    def test_explicit_missing_provider_config_fails(self):
        result = self.run_inventory(json.dumps({"provider": {}}), explicit_provider=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("provider configuration file not found", result.stderr)

    def test_malformed_provider_config_fails_without_leaking_body(self):
        result = self.run_inventory(
            json.dumps({"provider": {}}),
            provider_content='{"config": {"secret": "' + SECRET,
            provider_missing=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("provider configuration file contains malformed JSON", result.stderr)
        self.assertNotIn(SECRET, result.stderr)

    def test_unsupported_provider_config_schema_fails_without_leaking_body(self):
        result = self.run_inventory(
            json.dumps({"provider": {}}),
            provider_content=json.dumps({"config": {"secret": SECRET}}),
            provider_missing=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("unsupported schema", result.stderr)
        self.assertNotIn(SECRET, result.stderr)

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
