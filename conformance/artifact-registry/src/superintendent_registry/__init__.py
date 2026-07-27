from .authority import AuthorityCore, AuthorityError
from .authority_registry import AuthorityRegistry
from .database import connect
from .registry import RegistryError, observe_file

Registry = AuthorityRegistry

__all__ = [
    "AuthorityCore",
    "AuthorityError",
    "AuthorityRegistry",
    "Registry",
    "RegistryError",
    "connect",
    "observe_file",
]
