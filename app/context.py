"""Request-scoped handle bundling a verified user id with a user-scoped DB client.

`UserContext` is the shared currency between `routes/` and `db/`: the route
dependency builds it from a validated JWT, and every `fetch_*` function takes
it as its first argument.
"""

from dataclasses import dataclass

from supabase import Client


@dataclass(frozen=True)
class UserContext:
    user_id: str
    db: Client
