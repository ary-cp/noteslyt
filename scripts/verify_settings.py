"""
Phase 2 verification — layered settings + first-user admin, over real HTTP.

Needs a running server (applies migration 0008 first):

    uv run python scripts/migrate.py
    uv run uvicorn app.main:app --port 8000
    uv run python scripts/verify_settings.py [http://localhost:8000]

Covers:
  * first signup is admin, second is not (/auth/me echoes is_admin)
  * GET /system/settings as admin (200, secrets masked, env-pinned keys locked)
  * non-admin GET/PATCH -> 403, anonymous -> 401
  * PATCH happy path: scalar + hot-reload still serves traffic afterwards
  * PATCH locked key / unknown key / invalid value -> 422, nothing applied
  * PATCH secret: stored encrypted at rest, GET shows configured (never plaintext)
  * PATCH null clears back to default
  * GET /system/settings/status as plain user (no secrets, drift bool present)
  * model change raises needs_reembed with a warning

Exits non-zero on the first failure. Cleans up the users + settings it creates
(direct DB deletes, so cleanup works even when an early check fails).
"""

from __future__ import annotations

import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TAG = uuid.uuid4().hex[:8]

failures: list[str] = []
created_keys: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
        raise SystemExit(1)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    tag = TAG
    c = httpx.Client(base_url=BASE, timeout=60)

    print("admin bootstrap")
    r1 = c.post("/auth/signup", json={
        "email": f"admin-{tag}@test.co", "password": "correct horse battery",
        "display_name": "Admin"})
    r1.raise_for_status()
    admin_tok = r1.json()["access_token"]
    me1 = c.get("/auth/me", headers=auth(admin_tok)).json()
    check("first signup is admin", me1.get("is_admin") is True, str(me1))

    r2 = c.post("/auth/signup", json={
        "email": f"user-{tag}@test.co", "password": "correct horse battery",
        "display_name": "User"})
    r2.raise_for_status()
    user_tok = r2.json()["access_token"]
    me2 = c.get("/auth/me", headers=auth(user_tok)).json()
    check("second signup is not admin", me2.get("is_admin") is False, str(me2))

    print("\nread access")
    g = c.get("/system/settings", headers=auth(admin_tok))
    check("admin GET 200", g.status_code == 200, str(g.status_code))
    items = {i["key"]: i for i in g.json()["settings"]}
    check("all items carry group/source/locked", all(
        {"group", "source", "locked", "secret"} <= set(i) for i in items.values()))
    leaked = [k for k, i in items.items() if i["secret"] and i["value"] not in (None, "")]
    check("no secret plaintext leaks", not leaked, str(leaked))
    # Keys pinned in this repo's .env must show locked (operator override wins).
    check("env-pinned llm_base_url locked", items["llm_base_url"]["locked"] is True)
    check("env-pinned llm_base_url source=env", items["llm_base_url"]["source"] == "env")
    # Empty-value rule at unit level: `FOO=` must not count as explicit.
    import sys as _sys0
    from pathlib import Path as _Path0

    _sys0.path.insert(0, str(_Path0(__file__).resolve().parents[1]))
    import tempfile as _tf

    from app.settings_overlay import _dotenv_keys as _dk
    with _tf.NamedTemporaryFile("w", suffix=".env", delete=False) as _f:
        _f.write("# comment\nEMPTY_KEY=\nSET_KEY=x\n")
        _tmp = _f.name
    _dk.cache_clear()
    try:
        _parsed = _dk(_tmp)
        check("empty dotenv value is not explicit",
              "EMPTY_KEY" not in _parsed and "SET_KEY" in _parsed, str(sorted(_parsed)))
    finally:
        _dk.cache_clear()
    import os as _os

    _os.unlink(_tmp)

    r = c.get("/system/settings", headers=auth(user_tok))
    check("non-admin GET -> 403", r.status_code == 403, str(r.status_code))
    r = c.get("/system/settings")
    check("anonymous GET -> 401", r.status_code == 401, str(r.status_code))

    print("\nPATCH happy path + hot reload")
    p = c.patch("/system/settings", headers=auth(admin_tok),
                json={"top_k": 9, "llm_temperature": 0.33})
    check("PATCH 200", p.status_code == 200, p.text[:200])
    body = p.json()
    check("applied map", body["applied"] == {"top_k": "db", "llm_temperature": "db"}, str(body))
    created_keys.extend(["top_k", "llm_temperature"])
    g2 = {i["key"]: i for i in
          c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
    check("top_k now db-sourced", g2["top_k"]["source"] == "db" and g2["top_k"]["value"] == 9)
    h = c.get("/health")
    check("server still healthy after hot-reload", h.status_code == 200
          and h.json()["status"] == "ok", h.text[:120])
    # A plain user turn path still resolves (auth/me as smoke for rebuilt state).
    check("auth still works post-reload",
          c.get("/auth/me", headers=auth(user_tok)).status_code == 200)

    r = c.patch("/system/settings", headers=auth(user_tok), json={"top_k": 3})
    check("non-admin PATCH -> 403", r.status_code == 403, str(r.status_code))
    r = c.patch("/system/settings", json={"top_k": 3})
    check("anonymous PATCH -> 401", r.status_code == 401, str(r.status_code))

    print("\nPATCH refusals (nothing half-applies)")
    before = c.get("/system/settings", headers=auth(admin_tok)).json()
    r = c.patch("/system/settings", headers=auth(admin_tok),
                json={"llm_base_url": "http://evil:1/v1"})
    check("locked key -> 422", r.status_code == 422, r.text[:160])
    r = c.patch("/system/settings", headers=auth(admin_tok), json={"nope_not_real": 1})
    check("unknown key -> 422", r.status_code == 422, r.text[:160])
    r = c.patch("/system/settings", headers=auth(admin_tok), json={"top_k": "many"})
    check("invalid value -> 422", r.status_code == 422, r.text[:160])
    after = c.get("/system/settings", headers=auth(admin_tok)).json()
    check("refused PATCHes changed nothing", before == after)

    print("\nsecrets at rest")
    admin_items = {i["key"]: i for i in
                   c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
    unlocked_secrets = [k for k, i in admin_items.items() if i["secret"] and not i["locked"]]
    if unlocked_secrets:
        skey = unlocked_secrets[0]
        p = c.patch("/system/settings", headers=auth(admin_tok),
                    json={skey: f"sk-test-{tag}"})
        check(f"secret PATCH 200 ({skey})", p.status_code == 200, p.text[:200])
        created_keys.append(skey)
        g3 = {i["key"]: i for i in
              c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
        check("secret masked in GET", g3[skey]["value"] is None
              and g3[skey]["configured"] is True)
        import psycopg  # noqa: E402 — direct row check, bypassing the API
        import sys as _sys  # noqa: E402
        from pathlib import Path as _Path  # noqa: E402

        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
        from app.config import get_settings as _get_settings  # noqa: E402

        with psycopg.connect(_get_settings().database_url) as conn:
            raw = conn.execute(
                "select value from app_settings where key = %s", (skey,)).fetchone()[0]
        check("secret encrypted at rest", raw.startswith("enc1:") and tag not in raw,
              raw[:20] + "...")
    else:
        # Every secret is operator-pinned here: assert the refusal, and prove
        # the crypto round-trip at unit level instead of over HTTP.
        r = c.patch("/system/settings", headers=auth(admin_tok),
                    json={"llm_api_key": "sk-test-should-refuse"})
        check("pinned secret PATCH -> 422", r.status_code == 422, r.text[:160])
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
        from app.config import get_settings as _get_settings
        from app.settings_overlay import (  # noqa: E402
            coerce_patch_value, decrypt_secret, encrypt_secret, parse_stored_value,
        )

        _s = _get_settings()
        blob = encrypt_secret("sk-unit-test", _s)
        check("encrypt prefixes enc1:", blob.startswith("enc1:"))
        check("decrypt round-trips", decrypt_secret(blob, _s) == "sk-unit-test")
        check("parse decrypts stored secrets",
              parse_stored_value("llm_api_key", blob, _s) == "sk-unit-test")
        try:
            coerce_patch_value("top_k", "many", _s)
            check("invalid coercion raises", False)
        except ValueError:
            check("invalid coercion raises", True)

    print("\nclear back to default")
    p = c.patch("/system/settings", headers=auth(admin_tok), json={"top_k": None})
    check("clear 200 + cleared marker", p.status_code == 200
          and p.json()["applied"] == {"top_k": "cleared"}, p.text[:160])
    created_keys.remove("top_k")
    g4 = {i["key"]: i for i in
          c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
    check("top_k back to default 6", g4["top_k"]["source"] == "default"
          and g4["top_k"]["value"] == 6, str(g4["top_k"]))

    print("\nembedding status (plain user, no secrets)")
    s = c.get("/system/settings/status", headers=auth(user_tok))
    check("status 200", s.status_code == 200, s.text[:160])
    sj = s.json()
    check("status shape", {"embedding_model", "resolved_dim", "live_widths",
                           "drift", "detail"} <= set(sj), str(sorted(sj)))
    check("no drift on healthy test db", sj["drift"] is False, sj["detail"])
    check("live widths present", sj["live_widths"].get("chunks.embedding") == 768,
          str(sj["live_widths"]))

    print("\nreembed warning on model change")
    admin_items2 = {i["key"]: i for i in
                    c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
    from app.settings_overlay import REEMBED_SENSITIVE as _RS
    if not admin_items2["embedding_dim"]["locked"]:
        # Strongest signal: pin a wrong width, watch status flip to drift, revert.
        p = c.patch("/system/settings", headers=auth(admin_tok),
                    json={"embedding_dim": 1536})
        check("dim PATCH 200", p.status_code == 200, p.text[:200])
        created_keys.append("embedding_dim")
        check("needs_reembed raised", p.json()["needs_reembed"] is True, str(p.json()))
        s2 = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("status shows drift on wrong width",
              s2["drift"] is True and s2["resolved_dim"] == 1536, str(s2))
        c.patch("/system/settings", headers=auth(admin_tok), json={"embedding_dim": None})
        created_keys.remove("embedding_dim")
        s3 = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("drift clears after revert", s3["drift"] is False, str(s3))
    else:
        candidates = [k for k in sorted(_RS)
                      if not admin_items2[k]["locked"] and k != "embedding_dim"]
        if candidates:
            mkey = candidates[0]
            old_val = admin_items2[mkey]["value"]
            new_val = ("native" if old_val == "transcript" else "transcript"
                       if mkey.endswith("_mode") else "some-other-model")
            p = c.patch("/system/settings", headers=auth(admin_tok), json={mkey: new_val})
            check(f"model PATCH 200 ({mkey})", p.status_code == 200, p.text[:200])
            created_keys.append(mkey)
            check("needs_reembed raised", p.json()["needs_reembed"] is True
                  and p.json()["warnings"], str(p.json()))
            c.patch("/system/settings", headers=auth(admin_tok), json={mkey: None})
            created_keys.remove(mkey)
        else:
            r = c.patch("/system/settings", headers=auth(admin_tok),
                        json={"embedding_model": "some-other-model"})
            check("pinned model PATCH -> 422", r.status_code == 422, r.text[:160])

    print("\nSETTINGS VERIFIED")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    finally:
        # Best-effort cleanup even on failure (direct DB deletes).
        try:
            import sys as _sys2
            from pathlib import Path as _Path2

            _sys2.path.insert(0, str(_Path2(__file__).resolve().parents[1]))
            import psycopg as _psycopg

            from app.config import get_settings as _gs

            with _psycopg.connect(_gs().database_url) as _conn:
                with _conn.transaction():
                    if created_keys:
                        _conn.execute(
                            "delete from app_settings where key = any(%s)",
                            (created_keys,))
                    _conn.execute(
                        "delete from users where email like '%-{}@test.co'".format(TAG))
        except Exception as exc:  # noqa: BLE001 — cleanup must not mask results
            print(f"  cleanup warning: {exc}")
    raise SystemExit(code)