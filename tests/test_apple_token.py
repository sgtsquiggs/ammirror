from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ammirror.apple.token import load_developer_token, make_developer_token
from ammirror.config import AppleConfig
from ammirror.errors import ConfigError


def _keypair() -> tuple[str, ec.EllipticCurvePublicKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


def test_token_header_and_claims() -> None:
    pem, pub = _keypair()
    token = make_developer_token(pem, "KEY1234567", "TEAM123456", now=1_700_000_000, ttl=600)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEY1234567"
    claims = jwt.decode(token, pub, algorithms=["ES256"], options={"verify_exp": False})
    assert claims == {"iss": "TEAM123456", "iat": 1_700_000_000, "exp": 1_700_000_600}


def test_ttl_capped_at_apple_maximum() -> None:
    pem, pub = _keypair()
    token = make_developer_token(pem, "K", "T", now=0, ttl=10**9)
    claims = jwt.decode(token, pub, algorithms=["ES256"], options={"verify_exp": False})
    assert claims["exp"] == 15_777_000


def test_load_developer_token_reads_key(tmp_path: Path) -> None:
    pem, _ = _keypair()
    key = tmp_path / "AuthKey.p8"
    key.write_text(pem)
    token = load_developer_token(AppleConfig(key, "K", "T"))
    assert jwt.get_unverified_header(token)["kid"] == "K"


def test_load_developer_token_missing_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="MusicKit key"):
        load_developer_token(AppleConfig(tmp_path / "missing.p8", "K", "T"))
