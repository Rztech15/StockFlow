"""Authentication abstraction (types only; no implementation in Phase 0).

Layers: 1 authentication, 2 application identity, 3 workspace membership, 4 roles and
permissions, 5 authorization. Only layer 1 depends on the provider. A provider proves who
someone is and returns an AuthAssertion; the app maps it to its own user and issues ITS OWN
session. Phase 1 adds a local password provider; an external IdP can replace it later.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthAssertion:
    provider: str  # e.g. "local-password"
    subject: str  # the provider's stable id for the person
    email: str
    email_verified: bool  # never link accounts by email unless this is True


class IdentityProvider(Protocol):
    id: str

    def authenticate(self, credentials: object) -> AuthAssertion:
        """Return an assertion or raise app.errors.unauthorized()."""
        ...
