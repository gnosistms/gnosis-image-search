"""Credential loading for development and packaged search backends.

Release builds keep the encrypted payload and its decryption key in separate
files. This is deliberate application-level obfuscation, not protection from a
determined user who can inspect the installed application.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path


_AAD = b"gnosis-image-search-credentials-v1"
_PAYLOAD_NAME = "providers.enc"
_RUNTIME_KEY_NAME = "runtime.key"


def _credentials_dir() -> Path:
    configured = os.environ.get("SEARCH_CREDENTIALS_DIR")
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "credentials"
    return Path(__file__).resolve().parent / "credentials"


def _plain_key_files() -> list[Path]:
    files = []
    if not getattr(sys, "frozen", False):
        project = Path(__file__).resolve().parent.parent
        files.extend((
            project / "keys.json",
            project.parent / "automatic-illustrator" / "keys.json",
        ))
    return files


def _read_json(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_encrypted() -> dict[str, str]:
    directory = _credentials_dir()
    try:
        package = json.loads(
            (directory / _PAYLOAD_NAME).read_text(encoding="utf-8")
        )
        runtime_key = base64.b64decode(
            (directory / _RUNTIME_KEY_NAME).read_text(encoding="ascii"),
            validate=True,
        )
        nonce = base64.b64decode(package["nonce"], validate=True)
        ciphertext = base64.b64decode(package["ciphertext"], validate=True)
        if package.get("version") != 1:
            return {}
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        plaintext = AESGCM(runtime_key).decrypt(nonce, ciphertext, _AAD)
        data = json.loads(plaintext.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_key(name: str) -> str:
    env_name = f"{name.upper()}_API_KEY"
    if value := os.environ.get(env_name, "").strip():
        return value
    if configured := os.environ.get("SEARCH_KEYS_FILE"):
        if value := str(
            _read_json(Path(configured).expanduser()).get(name, "")
        ).strip():
            return value
    if os.environ.get("SEARCH_CREDENTIALS_DIR"):
        return str(_read_encrypted().get(name, "")).strip()
    for path in _plain_key_files():
        if value := str(_read_json(path).get(name, "")).strip():
            return value
    return str(_read_encrypted().get(name, "")).strip()


def write_encrypted(credentials: dict[str, str], directory: Path) -> None:
    """Write an AES-GCM payload and separate runtime key for a release build."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    directory.mkdir(parents=True, exist_ok=True)
    runtime_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    plaintext = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(runtime_key).encrypt(nonce, plaintext, _AAD)
    package = {
        "version": 1,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    (directory / _PAYLOAD_NAME).write_text(
        json.dumps(package, separators=(",", ":")), encoding="utf-8"
    )
    (directory / _RUNTIME_KEY_NAME).write_text(
        base64.b64encode(runtime_key).decode("ascii"), encoding="ascii"
    )
    (directory / _RUNTIME_KEY_NAME).chmod(0o600)
