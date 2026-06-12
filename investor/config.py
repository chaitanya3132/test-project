"""Load config.yaml and expose it as a plain dict."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or ROOT / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)
