from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from products_cli.errors import CliError

_DEFAULT_HOME = Path.home() / ".products-cli"
_REQUIRED_FIELDS = ("base_url", "access_token", "refresh_token")


def config_dir() -> Path:
    override = os.environ.get("PRODUCTS_CLI_HOME")
    return Path(override) if override else _DEFAULT_HOME


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


@dataclass
class Credentials:
    base_url: str
    access_token: str
    refresh_token: str


def save_credentials(credentials: Credentials) -> None:
    directory = config_dir()
    path = credentials_path()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _restrict(directory, 0o700)
        temp = directory / (path.name + ".tmp")
        temp.write_text(
            json.dumps(
                {
                    "base_url": credentials.base_url,
                    "access_token": credentials.access_token,
                    "refresh_token": credentials.refresh_token,
                }
            ),
            encoding="utf-8",
        )
        _restrict(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        raise CliError(f"could not write credentials to {path}: {exc}") from exc


def load_credentials() -> Credentials:
    path = credentials_path()
    if not path.exists():
        raise CliError(
            "not logged in: run `products-cli login --base-url URL "
            "--username USER --password PASS` first"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"could not read credentials from {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            f"credentials at {path} are not valid JSON ({exc}); run `products-cli login` again"
        ) from exc

    if not isinstance(data, dict):
        raise CliError(
            f"credentials at {path} are malformed; run `products-cli login` again"
        )
    missing = [field for field in _REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise CliError(
            f"credentials at {path} are incomplete (missing: {', '.join(missing)}); "
            "run `products-cli login` again"
        )
    return Credentials(
        base_url=data["base_url"],
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
    )


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass
