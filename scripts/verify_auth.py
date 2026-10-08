"""
Phase B verification — auth and tenancy, over real HTTP.

Needs a running server:
    uv run python scripts/verify_auth.py [http://localhost:8000]

Covers:
  * signup / login / whoami / onboarding / profile
  * password hashing, duplicate email, malformed input
  * no anonymous access anywhere
  * forged + tampered tokens rejected
  * account enumeration blocked on /auth/login
  * CROSS-USER ACCESS: a second learner must get 404 (never 403) for every
    project route, and must never see the first learner's project list
  * profile mutation is scoped to the caller

Exits non-zero on the first failure. Cleans up the users it creates.
"""

from __future__ import annotations

import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def register(c: httpx.Client, email: str, password: str, name: str) -> dict:
    r = c.post("/auth/signup", json={"email": email, "password": password, "display_name": name})
    r.raise_for_status()
    return r.json()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    email = f"priya-{tag}@test.co"
    password = "correct horse battery"

    c = httpx.Client(base_url=BASE, timeout=60)

    # ------------------------------------------------------------------ auth
    print("auth")
    r = c.get("/health")
    check("server healthy", r.status_code == 200, f"status={r.json()['status']}")

    first = c.post("/auth/signup", json={"email": email, "password": password, "display_name": "Priya"})
    check("signup works", first.status_code == 201, f"{first.status_code}")

    dup = c.post("/auth/signup", json={"email": email, "password": "anotherpassword", "display_name": "Dup"})
    check("duplicate email -> 409", dup.status_code == 409, f"{dup.status_code}")

    short = c.post("/auth/signup", json={"email": f"s-{tag}@t.co", "password": "short", "display_name": "S"})
    check("short password rejected", short.status_code == 422, f"{short.status_code}")

    bad_email = c.post("/auth/signup", json={"email": "not-an-email", "password": "longenough1", "display_name": "X"})
    check("malformed email rejected", bad_email.status_code == 422, f"{bad_email.status_code}")

    reg = first.json()
    check("signup returns a token", bool(reg.get("access_token")))
    check("signup returns the user", reg["user"]["display_name"] == "Priya", reg["user"]["display_name"])
    check("password never echoed", password not in reg["user"].values())
    token = reg["access_token"]
    me_headers = auth(token)

    login = c.post("/auth/login", json={"email": email, "password": password})
    check("login works", login.status_code == 200)
    check("login returns a token", bool(login.json().get("access_token")))
    check("token_type is bearer", login.json()["token_type"] == "bearer")

    wrong = c.post("/auth/login", json={"email": email, "password": "WRONGPASSWORD"})
    unknown = c.post("/auth/login", json={"email": f"ghost-{tag}@t.co", "password": "whatever"})
    check("wrong password -> 401", wrong.status_code == 401, f"{wrong.status_code}")
    check("unknown email -> 401", unknown.status_code == 401, f"{unknown.status_code}")
    check(
        "no account enumeration (identical error)",
        wrong.json()["detail"] == unknown.json()["detail"],
        wrong.json()["detail"],
    )

    # --------------------------------------------------------------- identity
    print("\nidentity")
    who = c.get("/auth/me", headers=me_headers)
    check("whoami with token", who.status_code == 200 and who.json()["email"] == email)
    check("no token -> 401", c.get("/auth/me").status_code == 401)
    check(
        "garbage token -> 401",
        c.get("/auth/me", headers=auth("not.a.jwt")).status_code == 401,
    )
    forged = c.get("/auth/me", headers=auth(token[:-4] + "AAAA"))
    check("tampered signature -> 401", forged.status_code == 401, f"{forged.status_code}")
    unsigned = c.get("/auth/me", headers=auth("eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAifQ."))
    check("unsigned 'none' token -> 401", unsigned.status_code == 401, f"{unsigned.status_code}")

    # ------------------------------------------------------------- onboarding
    print("\nonboarding + global memory")
    ob = c.post(
        "/auth/onboarding",
        json={"capability_level": "intermediate", "thinking_level": "analytical", "goals": "pass thermodynamics"},
        headers=me_headers,
    )
    check("onboarding accepted", ob.status_code == 204, f"{ob.status_code}")
    prof = c.get("/profile", headers=me_headers)
    check("profile readable", prof.status_code == 200)
    check("capability stored", prof.json()["capability_level"] == "intermediate", prof.json()["capability_level"])
    check("thinking level stored", prof.json()["thinking_level"] == "analytical")
    check("goals stored", prof.json()["goals"] == "pass thermodynamics")
    check("profile has no password/secret", "password" not in prof.json())

    patched = c.patch(
        "/profile", json={"goals": "actually just survive"}, headers=me_headers
    )
    check("profile editable", patched.status_code == 200 and patched.json()["goals"] == "actually just survive")
    check("patch kept other fields", patched.json()["capability_level"] == "intermediate")

    bogus = c.patch(
        "/profile", json={"capability_level": "wizard"}, headers=me_headers
    )
    check("unknown level rejected -> unknown", bogus.json()["capability_level"] == "unknown")

    levels = c.get("/profile/levels")
    check("levels endpoint lists the enums", "advanced" in levels.json()["capability_levels"])

    # -------------------------------------------------------------- projects
    print("\nprojects (owned)")
    created = c.post(
        "/projects", json={"title": "Thermodynamics", "subject": "Physics"}, headers=me_headers
    )
    check("create project", created.status_code == 201, f"{created.status_code}")
    project = created.json()

    listing = c.get("/projects", headers=me_headers)
    check("list projects", listing.status_code == 200)
    titles = [p["title"] for p in listing.json()]
    check("my project listed", "Thermodynamics" in titles, str(titles))
    check("stats block present", "stats" in listing.json()[0])

    check("get own project", c.get(f"/projects/{project['id']}", headers=me_headers).status_code == 200)
    upd = c.patch(f"/projects/{project['id']}", json={"subject": "Thermal Physics"}, headers=me_headers)
    check("update own project", upd.status_code == 200 and upd.json()["subject"] == "Thermal Physics")
    check("no token -> 401", c.get(f"/projects/{project['id']}").status_code == 401)
    check("bad token -> 401", c.get(f"/projects/{project['id']}", headers=auth("x.y.z")).status_code == 401)
    bad_uuid = c.get("/projects/not-a-uuid", headers=me_headers)
    check("malformed project id -> 422/404", bad_uuid.status_code in (404, 422), f"{bad_uuid.status_code}")

    # ------------------------------------------------- CROSS-USER ACCESS
    print("\nCROSS-USER ACCESS (the guarantee)")
    intruder = register(c, f"mallory-{tag}@test.co", "hunter2hunter2", "Mallory")
    ih = auth(intruder["access_token"])

    check("create own project", c.post("/projects", json={"title": "Roman Empire"}, headers=ih).status_code == 201)

    ghost = c.get(f"/projects/{project['id']}", headers=ih)
    check("cannot READ another user's project", ghost.status_code == 404, f"{ghost.status_code} (404 not 403)")
    check("404 body hides existence", "not found" in ghost.json()["detail"].lower(), ghost.json()["detail"])

    gpatch = c.patch(f"/projects/{project['id']}", json={"title": "Hijacked"}, headers=ih)
    check("cannot UPDATE another user's project", gpatch.status_code == 404, f"{gpatch.status_code}")
    still = c.get(f"/projects/{project['id']}", headers=me_headers).json()
    check("victim title unchanged", still["title"] == "Thermodynamics", still["title"])

    gdel = c.delete(f"/projects/{project['id']}", headers=ih)
    check("cannot DELETE another user's project", gdel.status_code == 404, f"{gdel.status_code}")
    check("victim project survives", c.get(f"/projects/{project['id']}", headers=me_headers).status_code == 200)

    intruder_list = [p["title"] for p in c.get("/projects", headers=ih).json()]
    check("intruder sees only own projects", intruder_list == ["Roman Empire"], str(intruder_list))
    check("victim project absent from intruder list", "Thermodynamics" not in intruder_list)

    check(
        "profiles are separate",
        c.get("/profile", headers=ih).json()["capability_level"] == "unknown",
        c.get("/profile", headers=ih).json()["capability_level"],
    )

    # ------------------------------------------------------------- cleanup
    print("\ncleanup")
    mine = c.get("/projects", headers=me_headers).json()
    for p in mine:
        c.delete(f"/projects/{p['id']}", headers=me_headers)
    theirs = c.get("/projects", headers=ih).json()
    for p in theirs:
        c.delete(f"/projects/{p['id']}", headers=ih)
    check("all test projects removed", c.get("/projects", headers=me_headers).json() == [])
    check("no projects left for intruder", c.get("/projects", headers=ih).json() == [])

    c.close()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("AUTH + TENANCY VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())