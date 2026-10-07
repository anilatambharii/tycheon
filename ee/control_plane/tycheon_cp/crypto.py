"""Envelope encryption for bring-your-own credentials.

Each secret is sealed with its own random 256-bit data key (AES-256-GCM); the data key is wrapped
by a KMS key and only the wrapped form is stored. The additional authenticated data binds the
ciphertext to ``(org, credential, purpose)``: a row copied to another tenant, or a ciphertext
swapped between two credentials, fails to open even though the wrapped key is valid.

Providers:

* :class:`LocalKms` wraps data keys with a master key from the environment. Development and tests
  only; configuration refuses it in production.
* :class:`AwsKms` calls AWS KMS (``boto3``, the ``ee-aws`` extra). It is verified here against a
  stubbed client only, never against real AWS.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class CryptoError(Exception):
    """A secret could not be sealed or opened. The message never includes key material."""


class Kms(Protocol):
    """A key-wrapping service: it holds the master key and never reveals it."""

    key_id: str

    async def wrap(self, data_key: bytes, context: bytes) -> bytes: ...

    async def unwrap(self, wrapped: bytes, context: bytes) -> bytes: ...


class LocalKms:
    """Wraps data keys with AES-GCM under a master key supplied from the environment."""

    key_id = "local-dev"

    def __init__(self, master_key_b64: str) -> None:
        key = base64.b64decode(master_key_b64, validate=True)
        if len(key) != KEY_BYTES:
            raise CryptoError("the local master key must be 32 bytes")
        self._aead = AESGCM(key)

    async def wrap(self, data_key: bytes, context: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, data_key, context)

    async def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        try:
            return self._aead.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], context)
        except InvalidTag as exc:
            raise CryptoError("the wrapped key does not open in this context") from exc


class AwsKms:
    """AWS KMS key wrapping through ``Encrypt``/``Decrypt`` with an encryption context."""

    def __init__(self, key_id: str, client: Any | None = None) -> None:
        self.key_id = key_id
        if client is None:
            import boto3  # noqa: PLC0415 - the ee-aws extra; only needed with real AWS KMS

            client = boto3.client("kms")
        self._client = client

    async def wrap(self, data_key: bytes, context: bytes) -> bytes:
        response = await asyncio.to_thread(
            self._client.encrypt,
            KeyId=self.key_id,
            Plaintext=data_key,
            EncryptionContext={"ctx": base64.b64encode(context).decode()},
        )
        return bytes(response["CiphertextBlob"])

    async def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.decrypt,
                KeyId=self.key_id,
                CiphertextBlob=wrapped,
                EncryptionContext={"ctx": base64.b64encode(context).decode()},
            )
        except Exception as exc:  # boto raises many types; none may leak details
            raise CryptoError("KMS refused to unwrap the key") from exc
        return bytes(response["Plaintext"])


@dataclass(frozen=True)
class SealedSecret:
    """What is stored for a credential: nothing in it is usable without the KMS."""

    kms_key_id: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


def _aad(org_id: str, credential_id: str, purpose: str) -> bytes:
    return f"tycheon-cp/v1|{org_id}|{credential_id}|{purpose}".encode()


async def seal(
    kms: Kms, plaintext: bytes, *, org_id: str, credential_id: str, purpose: str = "credential"
) -> SealedSecret:
    """Encrypt ``plaintext`` for exactly one ``(org, credential, purpose)``."""
    aad = _aad(org_id, credential_id, purpose)
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
    return SealedSecret(
        kms_key_id=kms.key_id,
        wrapped_dek=await kms.wrap(data_key, aad),
        nonce=nonce,
        ciphertext=ciphertext,
    )


async def open_sealed(
    kms: Kms, sealed: SealedSecret, *, org_id: str, credential_id: str, purpose: str = "credential"
) -> bytes:
    """Decrypt a secret, but only for the ``(org, credential, purpose)`` it was sealed for."""
    aad = _aad(org_id, credential_id, purpose)
    data_key = await kms.unwrap(sealed.wrapped_dek, aad)
    try:
        return AESGCM(data_key).decrypt(sealed.nonce, sealed.ciphertext, aad)
    except InvalidTag as exc:
        raise CryptoError("the secret does not open in this context") from exc
