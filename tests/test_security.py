from app.security import hash_password, needs_rehash, verify_password


def test_argon2id_roundtrip():
    h = hash_password("s3cret-password!")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "s3cret-password!")
    assert not verify_password(h, "wrong")
    assert not verify_password("not-a-hash", "x")
    assert not needs_rehash(h)
