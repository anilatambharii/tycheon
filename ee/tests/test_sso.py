"""OIDC SSO against a fake identity provider (httpx MockTransport, a locally generated RSA key).

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tycheon_cp.sso import check_public_url

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

ISSUER = "http://idp.test"
CLIENT_ID = "tycheon-client"


class FakeIdp:
    """Serves discovery, JWKS and the token endpoint; the next ID token is set by each test."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.next_claims: dict | None = None
        self.sign_with = self.key
        self.alg = "RS256"
        self.token_status = 200
        self.seen_token_requests: list[dict[str, str]] = []

    def jwks(self) -> dict:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=self.jwks())
        if path == "/token":
            self.seen_token_requests.append(
                dict(parse_qs(request.content.decode(), keep_blank_values=True))
            )
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            token = jwt.encode(
                self.next_claims or {},
                self.sign_with,
                algorithm=self.alg,
                headers={"kid": "k1"},
            )
            return httpx.Response(200, json={"id_token": token, "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
async def idp(cp):
    fake = FakeIdp()
    cp.extras["http"] = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield fake
    await cp.extras["http"].aclose()


async def enterprise_org(signup_org, set_org_plan):
    org = await signup_org()
    await set_org_plan(org.org_id, "enterprise")
    return org


async def configure(client, org, domains=("example.com",), role="member"):
    return await client.put(
        "/v1/sso",
        json={
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": "idp-client-secret",
            "allowed_domains": list(domains),
            "default_role": role,
        },
        headers=org.session,
    )


async def start_login(client, slug):
    res = await client.get(f"/auth/oidc/{slug}/login")
    assert res.status_code == 302, res.text
    query = parse_qs(urlparse(res.headers["location"]).query)
    return {k: v[0] for k, v in query.items()}


def claims(nonce, **over):
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": f"idp-{uuid.uuid4().hex[:12]}",
        "email": f"user-{uuid.uuid4().hex[:12]}@example.com",
        "email_verified": True,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
    }
    base.update(over)
    return base


async def slug_of(client, org) -> str:
    return (await client.get("/v1/me", headers=org.session)).json()["org"]["slug"]


async def sign_in(client, idp, org, **over):
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    over = dict(over)
    idp.next_claims = claims(over.pop("nonce", params["nonce"]), **over)
    return await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": params["state"]}
    )


# -------------------------------------------------------------------------------- setup
async def test_sso_is_an_enterprise_feature(client, signup_org, idp) -> None:
    org = await signup_org()
    res = await configure(client, org)
    assert res.status_code == 403 and res.json()["error"]["code"] == "plan_feature"


async def test_configuring_sso_seals_the_client_secret(
    client, signup_org, set_org_plan, idp, owner
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    assert (await configure(client, org)).status_code == 204
    row = await owner.fetchrow("SELECT * FROM credentials WHERE org_id = $1::uuid", org.org_id)
    blob = b"".join(bytes(row[c]) for c in ("wrapped_dek", "nonce", "ciphertext"))
    assert b"idp-client-secret" not in blob
    shown = await client.get("/v1/credentials", headers=org.session)
    assert "idp-client-secret" not in shown.text


async def test_only_admins_can_configure_sso(client, signup_org, set_org_plan, idp, cp) -> None:
    from tycheon_cp.security import Session, issue_session

    org = await enterprise_org(signup_org, set_org_plan)
    member = issue_session(
        cp.session_secret, Session(org.user_id, org.org_id, "member"), ttl_seconds=60
    )
    res = await client.put(
        "/v1/sso",
        json={
            "issuer": ISSUER,
            "client_id": "c",
            "client_secret": "s",
            "allowed_domains": ["example.com"],
        },
        headers={"Authorization": f"Bearer {member}"},
    )
    assert res.status_code == 403


async def test_a_mismatched_discovery_issuer_is_refused(
    client, signup_org, set_org_plan, cp
) -> None:
    def lying(request):
        return httpx.Response(
            200,
            json={
                "issuer": "http://evil.test",
                "authorization_endpoint": "http://evil.test/a",
                "token_endpoint": "http://evil.test/t",
                "jwks_uri": "http://evil.test/j",
            },
        )

    cp.extras["http"] = httpx.AsyncClient(transport=httpx.MockTransport(lying))
    org = await enterprise_org(signup_org, set_org_plan)
    assert (await configure(client, org)).status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "ftp://idp.example.com",
        "http://idp.example.com",
        "https://user:pw@idp.example.com",
        "https:///x",
        "https://127.0.0.1",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest",
        "https://[::1]/",
    ],
)
def test_the_server_will_not_fetch_internal_or_insecure_urls_in_production(url) -> None:
    from tycheon_cp.errors import ApiProblem

    with pytest.raises(ApiProblem):
        check_public_url(url, env="production")


# ---------------------------------------------------------------------------- sign-in
async def test_a_valid_sso_login_provisions_the_user_into_the_org(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org, role="viewer")
    who = {"sub": f"idp-{uuid.uuid4().hex[:8]}", "email": f"ada-{uuid.uuid4().hex[:8]}@example.com"}
    res = await sign_in(client, idp, org, **who)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["org_id"] == org.org_id and body["role"] == "viewer"
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["email"] == who["email"]
    again = await sign_in(client, idp, org, **who)
    assert again.json()["user_id"] == body["user_id"]  # the same user, not a duplicate


async def test_the_login_redirect_uses_pkce_state_and_nonce(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    params = await start_login(client, await slug_of(client, org))
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) >= 40
    assert len(params["state"]) >= 24 and len(params["nonce"]) >= 24
    assert params["response_type"] == "code" and params["client_id"] == CLIENT_ID


async def test_the_token_request_proves_possession_with_the_pkce_verifier(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    idp.next_claims = claims(params["nonce"])
    await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": params["state"]}
    )
    sent = idp.seen_token_requests[-1]
    verifier = sent["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == params["code_challenge"] and sent["client_secret"] == ["idp-client-secret"]


@pytest.mark.parametrize(
    ("label", "over"),
    [
        ("wrong audience", {"aud": "someone-else"}),
        ("wrong issuer", {"iss": "http://evil.test"}),
        ("expired", {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}),
        ("wrong nonce", {"nonce": "not-the-nonce"}),
        ("email not verified", {"email_verified": False}),
        ("email unverified claim missing", {"email_verified": None}),
        ("domain not allowed", {"email": "mallory@evil.test"}),
    ],
)
async def test_a_bad_id_token_is_refused(
    client, signup_org, set_org_plan, idp, label, over
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    res = await sign_in(client, idp, org, **over)
    assert res.status_code in (401, 403), (label, res.text)
    assert "token" not in res.json()


async def test_a_token_signed_by_another_key_is_refused(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    idp.sign_with = idp.other_key
    assert (await sign_in(client, idp, org)).status_code == 401


def with_forged_token(cp, idp, forged: str) -> None:
    """Make the fake token endpoint hand back ``forged`` instead of a properly signed token."""
    original = idp.handler

    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"id_token": forged})
        return original(request)

    cp.extras["http"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_an_unsigned_token_is_refused(client, signup_org, set_org_plan, idp, cp) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    with_forged_token(
        cp,
        idp,
        jwt.encode(claims(params["nonce"]), key="", algorithm="none", headers={"kid": "k1"}),
    )
    res = await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": params["state"]}
    )
    assert res.status_code == 401


async def test_an_hs256_token_signed_with_the_public_key_is_refused(
    client, signup_org, set_org_plan, idp, cp
) -> None:
    """The classic algorithm-confusion attack: HMAC-sign with the RSA public key as the secret."""
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    public_pem = idp.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    body = b64(json.dumps(claims(params["nonce"])).encode())
    sig = b64(hmac.new(public_pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    with_forged_token(cp, idp, f"{head}.{body}.{sig}")
    res = await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": params["state"]}
    )
    assert res.status_code == 401


async def test_the_callback_needs_the_cookie_and_the_matching_state(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    idp.next_claims = claims(params["nonce"])
    wrong_state = await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": "forged"}
    )
    assert wrong_state.status_code == 400
    client.cookies.clear()
    no_cookie = await client.get(
        f"/auth/oidc/{slug}/callback", params={"code": "abc", "state": params["state"]}
    )
    assert no_cookie.status_code == 400


async def test_an_email_already_used_in_another_org_cannot_be_taken_over(
    client, signup_org, set_org_plan, idp
) -> None:
    victim = await signup_org()
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org, domains=("example.com",))
    res = await sign_in(client, idp, org, email=victim.email, sub="attacker-sub")
    assert res.status_code == 409


async def test_sso_logins_never_land_in_another_org(client, signup_org, set_org_plan, idp) -> None:
    a, b = (
        await enterprise_org(signup_org, set_org_plan),
        await enterprise_org(signup_org, set_org_plan),
    )
    await configure(client, a)
    await configure(client, b)
    res = await sign_in(client, idp, a, sub="same-subject", email="x1@example.com")
    assert res.json()["org_id"] == a.org_id
    res_b = await sign_in(client, idp, b, sub="same-subject", email="x2@example.com")
    assert res_b.json()["org_id"] == b.org_id and res_b.json()["user_id"] != res.json()["user_id"]


async def test_sso_for_an_org_without_it_does_not_exist(client) -> None:
    assert (await client.get("/auth/oidc/no-such-org/login")).status_code == 404


# --------------------------------------------------------------- the browser hand-off
async def browser_callback(client, idp, org, **over):
    slug = await slug_of(client, org)
    params = await start_login(client, slug)
    over = dict(over)
    idp.next_claims = claims(over.pop("nonce", params["nonce"]), **over)
    return await client.get(
        f"/auth/oidc/{slug}/callback",
        params={"code": "abc", "state": params["state"]},
        headers={"Accept": "text/html"},
    )


async def test_a_browser_is_sent_back_to_the_dashboard_with_a_ticket_not_a_session(
    client, signup_org, set_org_plan, idp, cp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    res = await browser_callback(client, idp, org)
    assert res.status_code == 302
    target = urlparse(res.headers["location"])
    assert f"{target.scheme}://{target.netloc}" == cp.settings.dashboard_url
    assert target.path == "/api/auth/sso/complete"
    ticket = parse_qs(target.query)["ticket"][0]
    payload = jwt.decode(ticket, options={"verify_signature": False})
    assert "token" not in json.dumps(payload) and payload["aud"] == "tycheon-sso-ticket"
    assert payload["exp"] - payload["iat"] <= 60


async def test_a_ticket_is_exchanged_for_a_session_exactly_once(
    client, signup_org, set_org_plan, idp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    await configure(client, org)
    res = await browser_callback(client, idp, org)
    ticket = parse_qs(urlparse(res.headers["location"]).query)["ticket"][0]
    first = await client.post("/auth/sso/exchange", json={"ticket": ticket})
    assert first.status_code == 200 and first.json()["org_id"] == org.org_id
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {first.json()['token']}"})
    assert me.status_code == 200
    again = await client.post("/auth/sso/exchange", json={"ticket": ticket})
    assert again.status_code == 401 and "already used" in again.json()["error"]["message"]


async def test_forged_expired_or_foreign_tickets_are_refused(
    client, signup_org, set_org_plan, idp, cp
) -> None:
    org = await enterprise_org(signup_org, set_org_plan)
    now = int(time.time())
    base = {
        "jti": "j" * 20,
        "org": org.org_id,
        "sub": org.user_id,
        "role": "owner",
        "iat": now,
        "exp": now + 60,
        "aud": "tycheon-sso-ticket",
    }
    cases = {
        "wrong secret": jwt.encode(base, "x" * 40, algorithm="HS256"),
        "expired": jwt.encode({**base, "exp": now - 10}, cp.session_secret, algorithm="HS256"),
        "wrong audience": jwt.encode(
            {**base, "aud": "tycheon-cp"}, cp.session_secret, algorithm="HS256"
        ),
        "garbage": "not.a.jwt" + "x" * 20,
    }
    for label, ticket in cases.items():
        res = await client.post("/auth/sso/exchange", json={"ticket": ticket})
        assert res.status_code == 401, label


# ----------------------------------------------------- outbound requests to identity providers
@pytest.mark.parametrize(
    "issuer",
    [
        "https://idp.example.com/?x=1",
        "https://idp.example.com/#frag",
        "https://idp.example.com/a b",
        "https://idp.example.com/../internal",
        "https://idp.example.com/a;p=1",
        "https://idp.example.com/%2e%2e/x",
    ],
)
def test_an_issuer_url_with_anything_but_a_plain_path_is_refused(issuer) -> None:
    from tycheon_cp.errors import ApiProblem
    from tycheon_cp.sso import clean_issuer

    with pytest.raises(ApiProblem):
        clean_issuer(issuer, env="dev")


def test_a_clean_issuer_is_rebuilt_from_its_parts() -> None:
    from tycheon_cp.sso import clean_issuer

    assert (
        clean_issuer(" http://idp.test/realms/acme/ ", env="dev") == "http://idp.test/realms/acme"
    )


async def test_in_production_the_request_goes_to_the_address_that_was_checked(
    cp, monkeypatch
) -> None:
    """DNS rebinding: resolve once, connect to that address, keep the name for TLS and Host."""
    import dataclasses

    from tycheon_cp import sso

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    cp.extras["http"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(sso, "public_addresses", lambda host, port: ["93.184.216.34"])
    cp.settings = dataclasses.replace(cp.settings, env="production", kms="aws", aws_kms_key_id="k")
    await sso.send(cp, "GET", "https://idp.example.com/.well-known/openid-configuration")
    request = seen[0]
    assert request.url.host == "93.184.216.34" and request.headers["host"] == "idp.example.com"
    assert request.extensions["sni_hostname"] == "idp.example.com"


async def test_in_production_a_private_resolution_is_refused_before_any_request(
    cp, monkeypatch
) -> None:
    import dataclasses

    from tycheon_cp import sso
    from tycheon_cp.errors import ApiProblem

    called: list[int] = []
    cp.extras["http"] = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: called.append(1) or httpx.Response(200, json={}))
    )
    monkeypatch.setattr(
        sso.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 443))]
    )
    cp.settings = dataclasses.replace(cp.settings, env="production", kms="aws", aws_kms_key_id="k")
    with pytest.raises(ApiProblem):
        await sso.send(cp, "GET", "https://rebinding.example.com/jwks")
    assert called == []
