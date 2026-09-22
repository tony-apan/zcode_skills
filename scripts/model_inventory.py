#!/usr/bin/env python3
"""Emit a sanitized inventory of configured ZCode model capabilities."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_CONFIG = Path.home() / ".zcode" / "v2" / "config.json"
PROVIDER_CONFIG_NAME = "provider_config.json"
INVENTORY_SCHEMA_VERSION = 1
INVENTORY_GENERATOR = "tony-agents-pack/model_inventory"


class InventoryError(Exception):
    pass


def optional_integer(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def unique_strings(value: Any) -> List[str]:
    result: List[str] = []
    for item in string_list(value):
        if item not in result:
            result.append(item)
    return result


def inventory_result(providers: List[Dict[str, Any]]) -> Dict[str, Any]:
    public_providers = []
    for provider in providers:
        public_providers.append(
            {
                "enabled": provider["enabled"],
                "id": provider["id"],
                "models": provider["models"],
            }
        )
    return {
        "generator": INVENTORY_GENERATOR,
        "providers": public_providers,
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "verification": "DECLARED_UNVERIFIED",
    }


def sanitize_model(name: str, value: Dict[str, Any]) -> Dict[str, Any]:
    limit = value.get("limit")
    modalities = value.get("modalities")
    reasoning = value.get("reasoning")
    context = optional_integer(limit.get("context")) if isinstance(limit, dict) else None
    inputs = string_list(modalities.get("input")) if isinstance(modalities, dict) else []
    variants = string_list(reasoning.get("variants")) if isinstance(reasoning, dict) else []
    return {
        "limit": {"context": context},
        "modalities": {"input": inputs},
        "name": name,
        "reasoning": {"variants": variants},
    }


def sanitize_config(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError("configuration root must be a JSON object")
    providers_value = value.get("provider", {})
    if not isinstance(providers_value, dict):
        raise InventoryError("provider section must be a JSON object")
    providers: List[Dict[str, Any]] = []
    for provider_id in sorted(providers_value):
        provider = providers_value[provider_id]
        if not isinstance(provider_id, str) or not isinstance(provider, dict):
            continue
        enabled_value = provider.get("enabled", True)
        if not isinstance(enabled_value, bool):
            raise InventoryError("provider enabled must be a boolean")
        models_value = provider.get("models", {})
        if not isinstance(models_value, dict):
            raise InventoryError("provider models must be a JSON object")
        models = []
        for model_name in sorted(models_value):
            model = models_value[model_name]
            if isinstance(model_name, str) and isinstance(model, dict):
                models.append(sanitize_model(model_name, model))
        providers.append({"enabled": enabled_value, "id": provider_id, "models": models})
    return inventory_result(providers)


def sanitize_provider_model(model_name: str, rule: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = rule.get("config", {}) if isinstance(rule, dict) else {}
    properties = config.get("properties", {}) if isinstance(config, dict) else {}
    option_specs = config.get("optionSpecs", {}) if isinstance(config, dict) else {}

    context = optional_integer(properties.get("contextWindow")) if isinstance(properties, dict) else None
    inputs: List[str] = []
    if isinstance(properties, dict):
        modalities = properties.get("modalities")
        if isinstance(modalities, dict):
            inputs = string_list(modalities.get("input"))
        if not inputs:
            inputs = string_list(properties.get("inputModalities"))

    variants: List[str] = []
    if isinstance(option_specs, dict):
        reasoning_level = option_specs.get("reasoningLevel")
        if isinstance(reasoning_level, dict):
            variants = string_list(reasoning_level.get("values"))

    return {
        "limit": {"context": context},
        "modalities": {"input": inputs},
        "name": model_name,
        "reasoning": {"variants": variants},
    }


def provider_config_payload(value: Dict[str, Any]) -> Dict[str, Any]:
    nested = value.get("config")
    return nested if isinstance(nested, dict) else value


def sanitize_provider_config(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError("provider configuration root must be a JSON object")
    payload = provider_config_payload(value)

    provider_rules_value = payload.get("providerConfigRules")
    model_rules_value = payload.get("modelConfigRules", {})
    if not isinstance(provider_rules_value, dict):
        raise InventoryError("provider configuration has an unsupported schema")
    provider_rules = provider_rules_value.get("providerRules")
    if not isinstance(provider_rules, list):
        raise InventoryError("provider rules must be a JSON array")
    if not isinstance(model_rules_value, dict):
        raise InventoryError("model rules must be a JSON object")

    rules_by_model: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for section in ("providerModelRules", "manualProviderModelRules"):
        rules = model_rules_value.get(section, [])
        if not isinstance(rules, list):
            raise InventoryError("model rule section must be a JSON array")
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            provider_id = rule.get("providerId")
            model_id = rule.get("modelId")
            if isinstance(provider_id, str) and isinstance(model_id, str):
                rules_by_model[(provider_id, model_id)] = rule

    providers: List[Dict[str, Any]] = []
    for rule in provider_rules:
        if not isinstance(rule, dict):
            continue
        provider_id = rule.get("providerId")
        config = rule.get("config", {})
        if not isinstance(provider_id, str) or not isinstance(config, dict):
            continue

        enabled_declared = "enabled" in config
        enabled_value = config.get("enabled", True)
        if not isinstance(enabled_value, bool):
            raise InventoryError("provider enabled must be a boolean")

        for field in ("personalModelIds", "modelOrder"):
            if field in config and not isinstance(config[field], list):
                raise InventoryError("provider {} must be a JSON array".format(field))
        model_list_declared = "personalModelIds" in config or "modelOrder" in config
        personal_ids = unique_strings(config.get("personalModelIds"))
        ordered_ids = unique_strings(config.get("modelOrder"))
        model_ids = personal_ids or ordered_ids
        if not model_list_declared:
            model_ids = [model_id for rule_provider, model_id in rules_by_model if rule_provider == provider_id]
        model_ids = sorted(model_ids)

        models = [sanitize_provider_model(model_id, rules_by_model.get((provider_id, model_id))) for model_id in model_ids]
        providers.append(
            {
                "_enabled_declared": enabled_declared,
                "_models_authoritative": model_list_declared or bool(model_ids),
                "enabled": enabled_value,
                "id": provider_id,
                "models": models,
            }
        )

    return {"providers": sorted(providers, key=lambda provider: provider["id"])}


def merge_model(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(primary)
    if result["limit"]["context"] is None:
        result["limit"] = dict(fallback["limit"])
    if not result["modalities"]["input"]:
        result["modalities"] = {"input": list(fallback["modalities"]["input"])}
    if not result["reasoning"]["variants"]:
        result["reasoning"] = {"variants": list(fallback["reasoning"]["variants"])}
    return result


def merge_inventories(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Dict[str, Any]] = {provider["id"]: provider for provider in primary["providers"]}
    for provider in fallback["providers"]:
        current = merged.get(provider["id"])
        if current is None:
            merged[provider["id"]] = provider
            continue
        existing_models = {model["name"]: model for model in current["models"]}
        if provider.get("_models_authoritative"):
            effective_models = []
            for fallback_model in provider["models"]:
                primary_model = existing_models.get(fallback_model["name"])
                effective_models.append(
                    merge_model(primary_model, fallback_model) if primary_model is not None else fallback_model
                )
            current["models"] = sorted(effective_models, key=lambda model: model["name"])
        if provider.get("_enabled_declared"):
            current["enabled"] = provider["enabled"]
    return inventory_result([merged[provider_id] for provider_id in sorted(merged)])


def read_json(path: Path, label: str) -> Any:
    try:
        data = path.expanduser().read_bytes()
    except FileNotFoundError:
        raise InventoryError("{} file not found".format(label))
    except PermissionError:
        raise InventoryError("{} file is not readable".format(label))
    except (OSError, RuntimeError):
        raise InventoryError("{} file could not be read".format(label))
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        raise InventoryError("{} file contains malformed JSON".format(label))
    except UnicodeError:
        raise InventoryError("{} file is not valid UTF-8 or UTF-16".format(label))


def load_inventory(path: Path, provider_config_path: Optional[Path] = None, provider_config_required: bool = False) -> Dict[str, Any]:
    provider_path = provider_config_path or path.with_name(PROVIDER_CONFIG_NAME)
    try:
        inventory = sanitize_config(read_json(path, "configuration"))
    except InventoryError as exc:
        if str(exc) != "configuration file not found" or not provider_path.expanduser().is_file():
            raise
        inventory = inventory_result([])

    try:
        fallback_value = read_json(provider_path, "provider configuration")
    except InventoryError as exc:
        if not provider_config_required and str(exc) == "provider configuration file not found":
            return inventory
        raise
    return merge_inventories(inventory, sanitize_provider_config(fallback_value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to the ZCode config file")
    parser.add_argument(
        "--provider-config",
        type=Path,
        help="path to provider_config.json (defaults to the config file directory)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = load_inventory(args.config, args.provider_config, args.provider_config is not None)
    except InventoryError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
