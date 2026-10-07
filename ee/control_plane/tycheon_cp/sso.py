"""OIDC single sign-on (Enterprise plan).

Authorization-code flow with PKCE, ``state`` and ``nonce``. The ID token must verify against the
provider's published keys (RS256/ES256 only, so neither ``none`` nor an HS256 key-confusion token
is ever accepted) and match issuer, audience, expiry and nonce, and the email must be verified and
in the organisation's allowed domains. Users are provisioned just-in-time into the organisation
that owns the login URL, never into another one.

The provider endpoints come from OIDC discovery and are checked first: https only and not a
private, loopback or link-local address (outside development), so SSO cannot be used to make the
server fetch internal URLs.

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads the route annotations.
import asyncio
import base64
import hashlib
import ipaddress
import re
import secrets
import socket
import time
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse, urlunparse
from uuid import UUID

import asyncpg
import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from tycheon_cp import store
from tycheon_cp.app import ControlPlane, _Deps, _session_out
from tycheon_cp.crypto import SealedSecret, open_sealed, seal
from tycheon_cp.errors import ApiProblem, forbidden, not_found
from tycheon_cp.security import Session, normalize_email

STATE_COOKIE = "tycheon_oidc"
ALLOWED_ALGS = ["RS256", "ES256"]
STATE_TTL = 600
HTTP_TIMEOUT = 10.0


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=20, max_length=4000)


class SsoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: str = Field(max_length=300)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(min_length=1, max_length=2000)
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    default_role: str = Field(default="member", pattern="^(admin|member|viewer)$")


_ISSUER_PATH = re.compile(r"^[A-Za-z0-9._~/-]*$")


def public_addresses(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to, refusing the lot if any is not publicly routable."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ApiProblem(422, "invalid_request", "That host does not resolve.") from exc
    found = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ApiProblem(422, "invalid_request", "That host is not a public address.")
        found.append(str(address))
    return found


def check_public_url(url: str, *, env: str) -> None:
    """Refuse URLs the server must not fetch (non-https, credentials, private networks)."""
    parsed = urlparse(url)
    local = env in ("dev", "test")
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ApiProblem(422, "invalid_request", "Identity-provider URLs must use https.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ApiProblem(422, "invalid_request", "That identity-provider URL is not valid.")
    if not local:
        public_addresses(parsed.hostname, parsed.port or 443)


def clean_issuer(value: str, *, env: str) -> str:
    """An issuer URL with nothing but scheme, host and a plain path: no query, fragment or userinfo.

    The discovery URL is built from these parts, never by pasting a customer-supplied string.
    """
    parsed = urlparse(value.strip())
    segments = parsed.path.split("/")
    if (
        parsed.query
        or parsed.fragment
        or parsed.params
        or ".." in segments
        or not _ISSUER_PATH.fullmatch(parsed.path)
    ):
        raise ApiProblem(422, "invalid_request", "That issuer URL is not valid.")
    check_public_url(value.strip(), env=env)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


async def send(cp: ControlPlane, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One outbound request to an identity provider, pinned to the address that was checked.

    Outside development the host is resolved once, every address must be public, and the request
    goes to that address with the original host name kept for TLS (SNI and certificate checks) and
    the ``Host`` header, so a DNS answer that changes between the check and the connection (DNS
    rebinding) cannot redirect the request to an internal address. Redirects are never followed.
    """
    parsed = urlparse(url)
    if cp.settings.env not in ("dev", "test") and parsed.hostname:
        addresses = await asyncio.to_thread(public_addresses, parsed.hostname, parsed.port or 443)
        netloc = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
        if parsed.port:
            netloc += f":{parsed.port}"
        headers = {**kwargs.pop("headers", {}), "Host": parsed.netloc}
        extensions = {**kwargs.pop("extensions", {}), "sni_hostname": parsed.hostname}
        url = urlunparse(parsed._replace(netloc=netloc))
        return await _client(cp).request(
            method, url, headers=headers, extensions=extensions, **kwargs
        )
    return await _client(cp).request(method, url, **kwargs)


def _client(cp: ControlPlane) -> httpx.AsyncClient:
    http = cp.extras.get("http")
    if http is None:
        http = cp.extras["http"] = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False)
    return http


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _state_token(cp: ControlPlane, slug: str, state: str, nonce: str, verifier: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "slug": slug,
            "state": state,
            "nonce": nonce,
            "cv": verifier,
            "iat": now,
            "exp": now + STATE_TTL,
            "aud": "tycheon-oidc-state",
        },
        cp.session_secret,
        algorithm="HS256",
    )


def _issue_ticket(cp: ControlPlane, session: Any) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "jti": secrets.token_urlsafe(24),
            "org": session.org_id,
            "sub": session.user_id,
            "role": session.role,
            "iat": now,
            "exp": now + 60,
            "aud": "tycheon-sso-ticket",
        },
        cp.session_secret,
        algorithm="HS256",
    )


def _read_state(cp: ControlPlane, token: str | None) -> dict[str, Any]:
    if not token:
        raise ApiProblem(400, "invalid_request", "The sign-in attempt has expired; start again.")
    try:
        return jwt.decode(
            token, cp.session_secret, algorithms=["HS256"], audience="tycheon-oidc-state"
        )
    except jwt.PyJWTError as exc:
        raise ApiProblem(400, "invalid_request", "The sign-in attempt is not valid.") from exc


async def _provider(cp: ControlPlane, slug: str) -> tuple[UUID, dict[str, Any]]:
    async with cp.db.anonymous() as conn:
        found = await conn.fetchrow("SELECT * FROM cp_org_by_slug($1)", slug)
    if found is None:
        raise not_found("organisation")
    org_id: UUID = found["org_id"]
    async with cp.db.tenant(org_id) as conn:
        row = await conn.fetchrow("SELECT * FROM oidc_providers")
    if row is None:
        raise ApiProblem(404, "not_found", "Single sign-on is not set up for this organisation.")
    return org_id, dict(row)


async def _verify_id_token(
    cp: ControlPlane, provider: dict[str, Any], id_token: str, nonce: str
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise ApiProblem(
            401, "unauthorized", "The identity provider sent an invalid token."
        ) from exc
    if header.get("alg") not in ALLOWED_ALGS:
        raise ApiProblem(
            401, "unauthorized", "The identity provider used an unsupported algorithm."
        )
    keys = (await send(cp, "GET", provider["jwks_uri"])).json().get("keys", [])
    kid = header.get("kid")
    candidates = [k for k in keys if kid is None or k.get("kid") == kid]
    last: Exception | None = None
    for jwk in candidates:
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                jwt.PyJWK(jwk).key,
                algorithms=ALLOWED_ALGS,
                audience=provider["client_id"],
                issuer=provider["issuer"],
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            last = exc
            continue
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ApiProblem(
                401, "unauthorized", "The sign-in response does not match this attempt."
            )
        return claims
    raise ApiProblem(401, "unauthorized", "The identity token could not be verified.") from last


def register_sso(app: FastAPI, cp: ControlPlane) -> None:
    deps = _Deps(cp)
    Admin = Annotated[store.Principal, Depends(deps.session_principal("admin"))]  # noqa: N806
    router = APIRouter()

    @router.put("/v1/sso", tags=["sso"], status_code=204)
    async def configure(body: SsoIn, who: Admin) -> Response:
        """Set the organisation's OIDC provider (Enterprise). The secret is sealed, never echoed."""
        await _configure(cp, who, body)
        return Response(status_code=204)

    @router.get("/auth/oidc/{slug}/login", tags=["sso"])
    async def login(slug: str, request: Request) -> Response:
        org_id, provider = await _provider(cp, slug)
        org = await store.load_org(cp.db, org_id, cp.plans)
        if not org.plan.has("sso"):
            raise ApiProblem(403, "plan_feature", "Single sign-on is not part of this plan.")
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        redirect_uri = f"{cp.settings.public_url}/auth/oidc/{slug}/callback"
        query = urlencode(
            {
                "response_type": "code",
                "client_id": provider["client_id"],
                "redirect_uri": redirect_uri,
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        response = RedirectResponse(
            f"{provider['authorization_endpoint']}?{query}", status_code=302
        )
        response.set_cookie(
            STATE_COOKIE,
            _state_token(cp, slug, state, nonce, verifier),
            max_age=STATE_TTL,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path=f"/auth/oidc/{slug}",
        )
        return response

    @router.get("/auth/oidc/{slug}/callback", tags=["sso"])
    async def callback(
        request: Request,
        slug: str,
        code: str,
        state: str,
        attempt: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
    ) -> Response:
        saved = _read_state(cp, attempt)
        if saved["slug"] != slug or not secrets.compare_digest(saved["state"], state):
            raise ApiProblem(
                400, "invalid_request", "The sign-in response does not match this attempt."
            )
        org_id, provider = await _provider(cp, slug)
        client_secret = await _client_secret(cp, org_id, provider)
        token_response, payload = await _exchange_code(
            cp,
            slug=slug,
            provider=provider,
            client_secret=client_secret,
            code=code,
            verifier=saved["cv"],
        )
        id_token = payload.get("id_token")
        if token_response.status_code != 200 or not isinstance(id_token, str):
            raise ApiProblem(401, "unauthorized", "The identity provider refused the sign-in.")
        claims = await _verify_id_token(cp, provider, id_token, saved["nonce"])
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise forbidden("Your identity provider has not verified your email address.")
        email = normalize_email(str(claims["email"]))
        if email.rsplit("@", 1)[1] not in provider["allowed_domains"]:
            raise forbidden("Your email domain is not allowed to sign in to this organisation.")
        session = await _provision(cp, org_id, provider, email, str(claims["sub"]))
        out = _session_out(cp, session.org_id, session.user_id, session.role)
        return _finish(cp, request, slug, out)

    @router.post("/auth/sso/exchange", tags=["sso"])
    async def exchange(body: TicketIn) -> JSONResponse:
        """Redeem a browser sign-in ticket (once) for a session."""
        try:
            claims = jwt.decode(
                body.ticket,
                cp.session_secret,
                algorithms=["HS256"],
                audience="tycheon-sso-ticket",
                options={"require": ["exp", "jti", "org", "sub", "role"]},
            )
        except jwt.PyJWTError as exc:
            raise ApiProblem(401, "unauthorized", "That sign-in ticket is not valid.") from exc
        async with cp.db.anonymous() as conn:
            fresh = await conn.fetchval("SELECT cp_redeem_ticket($1)", str(claims["jti"]))
        if not fresh:
            raise ApiProblem(401, "unauthorized", "That sign-in ticket was already used.")
        issued = _session_out(cp, claims["org"], claims["sub"], claims["role"])
        return JSONResponse(issued.model_dump())

    app.include_router(router)


async def _configure(cp: ControlPlane, who: store.Principal, body: SsoIn) -> None:
    org = await store.load_org(cp.db, who.org_id, cp.plans)
    if not org.plan.has("sso"):
        raise ApiProblem(403, "plan_feature", f"The {org.plan.name} plan does not include SSO.")
    issuer = clean_issuer(body.issuer, env=cp.settings.env)
    try:
        doc = (await send(cp, "GET", f"{issuer}/.well-known/openid-configuration")).json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiProblem(422, "invalid_request", "OIDC discovery failed for that issuer.") from exc
    for field_name in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        check_public_url(str(doc.get(field_name, "")), env=cp.settings.env)
    if str(doc["issuer"]).rstrip("/") != issuer:
        raise ApiProblem(422, "invalid_request", "The discovered issuer does not match.")
    domains = sorted({d.strip().lower() for d in body.allowed_domains if d.strip()})
    credential_id = UUID(bytes=secrets.token_bytes(16), version=4)
    sealed = await seal(
        cp.kms,
        body.client_secret.encode(),
        org_id=str(who.org_id),
        credential_id=str(credential_id),
        purpose="oidc-client-secret",
    )
    async with cp.db.tenant(who.org_id) as conn:
        await conn.execute(
            "INSERT INTO credentials (id, org_id, name, provider, kms_key_id, wrapped_dek, "
            "nonce, ciphertext) VALUES ($1, $2, $3, 'oidc', $4, $5, $6, $7) "
            "ON CONFLICT (org_id, name) DO UPDATE SET id = EXCLUDED.id, kms_key_id = "
            "EXCLUDED.kms_key_id, wrapped_dek = EXCLUDED.wrapped_dek, nonce = EXCLUDED.nonce, "
            "ciphertext = EXCLUDED.ciphertext",
            credential_id,
            who.org_id,
            "oidc-client-secret",
            sealed.kms_key_id,
            sealed.wrapped_dek,
            sealed.nonce,
            sealed.ciphertext,
        )
        await conn.execute(
            "INSERT INTO oidc_providers (org_id, issuer, client_id, jwks_uri, "
            "authorization_endpoint, token_endpoint, client_secret_credential_id, "
            "allowed_domains, default_role) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) "
            "ON CONFLICT (org_id) DO UPDATE SET issuer=$2, client_id=$3, jwks_uri=$4, "
            "authorization_endpoint=$5, token_endpoint=$6, client_secret_credential_id=$7, "
            "allowed_domains=$8, default_role=$9",
            who.org_id,
            issuer,
            body.client_id,
            doc["jwks_uri"],
            doc["authorization_endpoint"],
            doc["token_endpoint"],
            credential_id,
            domains,
            body.default_role,
        )
        await store.audit(conn, who, "sso.configure", issuer, {"domains": domains})


async def _client_secret(cp: ControlPlane, org_id: UUID, provider: dict[str, Any]) -> str:
    async with cp.db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            "SELECT kms_key_id, wrapped_dek, nonce, ciphertext FROM credentials WHERE id = $1",
            provider["client_secret_credential_id"],
        )
    if row is None:
        raise ApiProblem(500, "internal_error", "SSO is misconfigured.")
    plain = await open_sealed(
        cp.kms,
        SealedSecret(**dict(row)),
        org_id=str(org_id),
        credential_id=str(provider["client_secret_credential_id"]),
        purpose="oidc-client-secret",
    )
    return plain.decode()


async def _exchange_code(
    cp: ControlPlane,
    *,
    slug: str,
    provider: dict[str, Any],
    client_secret: str,
    code: str,
    verifier: str,
) -> tuple[httpx.Response, dict[str, Any]]:
    try:
        response = await send(
            cp,
            "POST",
            provider["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{cp.settings.public_url}/auth/oidc/{slug}/callback",
                "client_id": provider["client_id"],
                "client_secret": client_secret,
                "code_verifier": verifier,
            },
        )
        return response, dict(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiProblem(502, "bad_gateway", "The identity provider did not respond.") from exc


def _finish(cp: ControlPlane, request: Request, slug: str, out: Any) -> Response:
    """JSON for API clients; for a browser, a redirect to the dashboard with a one-time ticket."""
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(out.model_dump())
    # never the session itself in a URL: it would end up in history and logs
    query = urlencode({"ticket": _issue_ticket(cp, out)})
    done = RedirectResponse(
        f"{cp.settings.dashboard_url}/api/auth/sso/complete?{query}", status_code=302
    )
    done.delete_cookie(STATE_COOKIE, path=f"/auth/oidc/{slug}")
    return done


async def _provision(
    cp: ControlPlane, org_id: UUID, provider: dict[str, Any], email: str, subject: str
) -> Session:
    """The user for this identity: existing by subject, else created in *this* organisation."""
    async with cp.db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, role FROM users WHERE oidc_subject = $1 AND disabled_at IS NULL", subject
        )
        if row is None:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO users (org_id, email, oidc_subject, role) VALUES ($1,$2,$3,$4) "
                    "RETURNING id, role",
                    org_id,
                    email,
                    subject,
                    provider["default_role"],
                )
            except asyncpg.UniqueViolationError as exc:
                raise ApiProblem(
                    409, "conflict", "That email address already belongs to another account."
                ) from exc
    if row is None:  # pragma: no cover - the insert returns a row or raises
        raise ApiProblem(500, "internal_error", "Sign-in failed.")
    return Session(str(row["id"]), str(org_id), row["role"])
