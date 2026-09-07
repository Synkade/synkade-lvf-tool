"""
AES-256 helpers shared by the block-encryption logic in format.py.

We deliberately never implement AES ourselves (per spec): this wraps the
`cryptography` package (https://cryptography.io), a widely-used, audited
Python crypto library — an equally standard choice to pycryptodome for
this purpose. Swapping to pycryptodome later only means editing this file.

Two modes are supported, matching the `enc_flag` field in the .lvf header:
    AES-256-CTR  - stream cipher, cheap random-access, no integrity check
    AES-256-GCM  - authenticated (detects tampering/corruption), slight
                   overhead per block (16-byte tag) and needs the whole
                   block available before it can be verified/decrypted.

The key must always be exactly 32 bytes (256 bits). Key derivation from a
user-supplied passphrase (if the CLI/GUI accepts a passphrase instead of a
raw key file) is done with PBKDF2-HMAC-SHA256 so weak passphrases don't
directly become the AES key.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

KEY_SIZE = 32  # AES-256
GCM_TAG_SIZE = 16
PBKDF2_ITERATIONS = 200_000
# Fixed salt namespace for this tool. Using a per-project constant salt (not
# a shared-secret) is acceptable here because the "password" in this tool is
# really an embedded/distributed asset key, not a user account credential -
# the goal is masking the raw asset, not protecting a login.
_PBKDF2_SALT = b"lvf-tool-static-salt-v1"


class CryptoError(ValueError):
    pass


def derive_key_from_passphrase(passphrase: str) -> bytes:
    """Derive a 32-byte AES-256 key from an arbitrary-length passphrase."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=_PBKDF2_SALT,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def load_key(key_or_path: str) -> bytes:
    """
    Resolve a --key argument into a 32-byte AES key.

    - If it points to an existing file containing exactly 32 raw bytes,
      that file's content is used as-is.
    - Otherwise the string is treated as a passphrase and stretched with
      PBKDF2 into a 32-byte key.
    """
    if os.path.isfile(key_or_path):
        with open(key_or_path, "rb") as f:
            data = f.read()
        if len(data) == KEY_SIZE:
            return data
        # Not a raw 32-byte key file - fall back to treating its text
        # content as a passphrase.
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise CryptoError(
                f"Key file '{key_or_path}' is not a raw 32-byte key nor valid UTF-8 text"
            )
        return derive_key_from_passphrase(text)
    return derive_key_from_passphrase(key_or_path)


def encrypt_block_ctr(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    _check_key(key)
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def decrypt_block_ctr(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    # CTR mode is symmetric.
    return encrypt_block_ctr(key, iv, ciphertext)


def encrypt_block_gcm(key: bytes, iv: bytes, plaintext: bytes,
                       associated_data: bytes = b"") -> bytes:
    """Returns ciphertext with the 16-byte GCM tag appended."""
    _check_key(key)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    if associated_data:
        encryptor.authenticate_additional_data(associated_data)
    ct = encryptor.update(plaintext) + encryptor.finalize()
    return ct + encryptor.tag


def decrypt_block_gcm(key: bytes, iv: bytes, ciphertext_with_tag: bytes,
                       associated_data: bytes = b"") -> bytes:
    _check_key(key)
    if len(ciphertext_with_tag) < GCM_TAG_SIZE:
        raise CryptoError("GCM block too short to contain an auth tag")
    ct, tag = ciphertext_with_tag[:-GCM_TAG_SIZE], ciphertext_with_tag[-GCM_TAG_SIZE:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag))
    decryptor = cipher.decryptor()
    if associated_data:
        decryptor.authenticate_additional_data(associated_data)
    try:
        return decryptor.update(ct) + decryptor.finalize()
    except Exception as exc:  # InvalidTag, etc.
        raise CryptoError(f"GCM authentication failed (corrupted/tampered block): {exc}")


def _check_key(key: bytes) -> None:
    if len(key) != KEY_SIZE:
        raise CryptoError(f"AES-256 key must be {KEY_SIZE} bytes, got {len(key)}")
