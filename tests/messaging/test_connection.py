from backend.messaging.connection import nats_auth_options


def test_nats_auth_options_omit_empty_seed() -> None:
    assert nats_auth_options("") == {}


def test_nats_auth_options_pass_nkey_seed_string() -> None:
    assert nats_auth_options("SUASEED") == {"nkeys_seed_str": "SUASEED"}
