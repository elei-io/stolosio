from typing import Any


def nats_auth_options(seed: str) -> dict[str, Any]:
    if not seed:
        return {}
    return {"nkeys_seed_str": seed}
