#!/usr/bin/env python3
"""Emit a sanitized inventory of configured ZCode model capabilities."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional


DEFAULT_CONFIG = Path.home() / ".zcode" / "v2" / "config.json"


class InventoryError(Exception):
    pass


def optional_integer(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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
    return {"providers": providers}


def load_inventory(path: Path) -> Dict[str, Any]:
    try:
        with path.expanduser().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise InventoryError("configuration file not found")
    except PermissionError:
        raise InventoryError("configuration file is not readable")
    except json.JSONDecodeError:
        raise InventoryError("configuration file contains malformed JSON")
    except OSError:
        raise InventoryError("configuration file could not be read")
    return sanitize_config(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to the ZCode config file")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = load_inventory(args.config)
    except InventoryError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
