"""Password hashing: Argon2id over an HMAC-SHA256 "pepper" pre-hash.

Stored format: "<pepper version>$<argon2 encoded hash>", for example
"pepper1$$argon2id$v=19$m=65536,t=3,p=4$...". The version prefix lets the pepper be rotated
later without invalidating existing hashes. Plaintext passwords and the pepper are never
logged or returned.
"""

import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

CURRENT_VERSION = "pepper1"


class PasswordService:
    def __init__(self, pepper_v1: str) -> None:
        self._peppers = {CURRENT_VERSION: pepper_v1.encode("utf-8")}
        self._hasher = PasswordHasher()
        # Verified against when the account does not exist, so timing does not reveal it.
        self._dummy = self._hasher.hash("timing-equalizer")

    def _digest(self, version: str, password: str) -> str:
        key = self._peppers[version]
        return hmac.new(key, password.encode("utf-8"), hashlib.sha256).hexdigest()

    def hash(self, password: str) -> str:
        encoded = self._hasher.hash(self._digest(CURRENT_VERSION, password))
        return f"{CURRENT_VERSION}${encoded}"

    def verify(self, stored: str, password: str) -> bool:
        version, separator, encoded = stored.partition("$")
        if not separator or version not in self._peppers:
            return False
        try:
            return self._hasher.verify(encoded, self._digest(version, password))
        except (VerificationError, InvalidHashError):
            return False

    def verify_dummy(self, password: str) -> None:
        try:
            self._hasher.verify(self._dummy, self._digest(CURRENT_VERSION, password))
        except (VerificationError, InvalidHashError):
            pass
