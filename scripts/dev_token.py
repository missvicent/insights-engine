"""Mint a local HS256 JWT for manual `curl` testing of the insights API.

Reads SUPABASE_JWT_SECRET from .env. The resulting token is signed with the
same secret the backend validates against, so it passes local auth — but it
is NOT a real Supabase-issued token and will not work against production.

Usage:
    python scripts/dev_token.py                 # sub=dev-user, 1h expiry
    python scripts/dev_token.py --sub abc-123   # specific user
    python scripts/dev_token.py --exp 7200      # 2h expiry

Then:
    curl -H "Authorization: Bearer $(python scripts/dev_token.py)" \\
        "http://localhost:8000/insights?budget_id=...&window=30d"
"""

from __future__ import annotations

import argparse
import sys
import time

import jwt
from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sub", default="dev-user", help="JWT sub claim (user id)")
    parser.add_argument("--exp", type=int, default=3600, help="Seconds until expiry")
    args = parser.parse_args()

    env = dotenv_values(".env")
    secret = env.get("SUPABASE_JWT_SECRET")
    if not secret:
        print("error: SUPABASE_JWT_SECRET not set in .env", file=sys.stderr)
        return 1

    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + args.exp,
        "sub": args.sub,
        "aud": "authenticated",
    }
    print(jwt.encode(payload, secret, algorithm="HS256"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
