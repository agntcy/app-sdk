# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import hashlib
import warnings
import weakref

import click
import datetime
import json
import os
from typing import Any, cast

import slim_bindings

from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)

PLACEHOLDER_SHARED_SECRET = "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"
SLIM_SHARED_SECRET_ENV = "SLIM_SHARED_SECRET"


def resolve_shared_secret(secret: str | None = None) -> str:
    """The SLIM shared secret to use: *secret*, else ``SLIM_SHARED_SECRET``.

    Falls back to the public :data:`PLACEHOLDER_SHARED_SECRET`, which only
    suits demos and local tests: any peer with the default can join your
    sessions.  Read at call time, so an env var set after import applies.
    """
    resolved = secret or os.environ.get(SLIM_SHARED_SECRET_ENV)
    if not resolved or resolved == PLACEHOLDER_SHARED_SECRET:
        logger.warning(
            "Using the placeholder SLIM shared secret; any peer with the "
            "default can join your sessions. Pass shared_secret_identity, "
            "SlimRpcConfig.secret or CardBuilder .with_shared_secret(), or set "
            "the SLIM_SHARED_SECRET env var."
        )
        return PLACEHOLDER_SHARED_SECRET
    return resolved


# Split an ID into its components
# Expected format: organization/namespace/application
# Raises ValueError if the format is incorrect
# Returns a Name with the 3 components
def split_id(id: str) -> slim_bindings.Name:
    try:
        organization, namespace, app = id.split("/")
    except ValueError as e:
        raise e

    return slim_bindings.Name(organization, namespace, app)


# Create a shared secret identity provider and verifier
# This is used for shared secret authentication
# Takes an identity and a shared secret as parameters
# Returns a tuple of (provider, verifier)
# This is used for shared secret authentication
def shared_secret_identity(
    identity: str, secret: str
) -> tuple[slim_bindings.IdentityProviderConfig, slim_bindings.IdentityVerifierConfig]:
    """
    Create a provider and verifier using a shared secret.
    """
    provider = cast(
        slim_bindings.IdentityProviderConfig,
        slim_bindings.IdentityProviderConfig.SHARED_SECRET(id=identity, data=secret),
    )
    verifier = cast(
        slim_bindings.IdentityVerifierConfig,
        slim_bindings.IdentityVerifierConfig.SHARED_SECRET(id=identity, data=secret),
    )

    return provider, verifier


# Create a JWT identity provider and verifier
# This is used for JWT authentication
# Takes private key path, public key path, and algorithm as parameters
# Returns a Slim object with the provider and verifier
def jwt_identity(
    jwt_path: str,
    jwk_path: str,
    local_name: str,
    iss: str | None = None,
    sub: str | None = None,
    aud: list[str] | None = None,
) -> tuple[slim_bindings.IdentityProviderConfig, slim_bindings.IdentityVerifierConfig]:
    """
    Parse the JWK and JWT from the provided strings.
    """

    with open(jwk_path) as jwk_file:
        jwk_string = jwk_file.read()

    # The JWK is normally encoded as base64, so we need to decode it
    spire_jwks: dict[str, str] = json.loads(jwk_string)
    try:
        encoded_jwks = next(iter(spire_jwks.values()))
    except StopIteration:
        raise ValueError(f"JWK file {jwk_path} contains no keys") from None
    jwks = base64.b64decode(encoded_jwks).decode("utf-8")

    # Read the static JWT file for signing
    with open(jwt_path) as jwt_file:
        jwt_content = jwt_file.read()

    # Create encoding key config for JWT signing
    encoding_key_config = slim_bindings.JwtKeyConfig(
        algorithm=slim_bindings.JwtAlgorithm.RS256,
        format=slim_bindings.JwtKeyFormat.PEM,
        key=cast(
            slim_bindings.JwtKeyData, slim_bindings.JwtKeyData.DATA(value=jwt_content)
        ),
    )

    # Create provider config for JWT authentication
    provider_config = cast(
        slim_bindings.IdentityProviderConfig,
        slim_bindings.IdentityProviderConfig.JWT(
            config=slim_bindings.ClientJwtAuth(
                key=cast(
                    slim_bindings.JwtKeyType,
                    slim_bindings.JwtKeyType.ENCODING(key=encoding_key_config),
                ),
                audience=aud or ["default-audience"],
                issuer=iss or "default-issuer",
                subject=sub or local_name,
                duration=datetime.timedelta(seconds=3600),
            )
        ),
    )

    # Create decoding key config for JWKS verification
    decoding_key_config = slim_bindings.JwtKeyConfig(
        algorithm=slim_bindings.JwtAlgorithm.RS256,
        format=slim_bindings.JwtKeyFormat.JWKS,
        key=cast(slim_bindings.JwtKeyData, slim_bindings.JwtKeyData.DATA(value=jwks)),
    )

    # Create verifier config
    verifier_config = cast(
        slim_bindings.IdentityVerifierConfig,
        slim_bindings.IdentityVerifierConfig.JWT(
            config=slim_bindings.JwtAuth(
                key=cast(
                    slim_bindings.JwtKeyType,
                    slim_bindings.JwtKeyType.DECODING(key=decoding_key_config),
                ),
                audience=aud or ["default-audience"],
                issuer=iss or "default-issuer",
                subject=sub,
                duration=datetime.timedelta(seconds=3600),
            )
        ),
    )

    return provider_config, verifier_config


# A custom click parameter type for parsing dictionaries from JSON strings
# This is useful for passing complex configurations via command line arguments
class DictParamType(click.ParamType):
    name = "dict"

    def convert(
        self, value: Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> Any:
        import json

        if isinstance(value, dict):
            return value  # Already a dict (for default value)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            self.fail(f"{value} is not valid JSON", param, ctx)


global_slim: slim_bindings.App | None = None
global_connection_id: int | None = None
global_slim_service: slim_bindings.Service | None = None
# ``insecure`` flag the cached connection above was opened with.
global_slim_insecure: bool | None = None
global_slim_endpoint: str | None = None
# Credentials the cached connection above was opened with; the shared secret
# is kept only as a SHA-256 digest.
global_slim_auth: tuple[Any, ...] | None = None
# Shares of the cached connection above: one per ``get_or_create_slim_instance``
# call that returned it and has not been released yet.
global_slim_users: int = 0
# Shares still held on connections that were retired from the cache (see
# :func:`retire_slim_connection`), by connection id.  A retired connection is
# disconnected when its last share is released.
_retired_connection_users: dict[int, int] = {}


def release_slim_connection(connection_id: int) -> bool:
    """Give back one share of the connection *connection_id*.

    Every :class:`SLIMTransport` in a process shares one connection, and each
    holds one share of it.  Disconnecting it on every ``close()`` would cut the
    line under the transports that still use it, so only the **last** share to
    be released may disconnect.

    Returns ``True`` when the caller held the last share and must now call
    ``service.disconnect(connection_id)``; the cached connection is already
    forgotten by then, so the next transport opens a new one even if the
    disconnect fails.  Returns ``False`` while other transports still hold a
    share: the caller must leave the connection open.

    A *connection_id* the cache does not know (it was forgotten by hand, or
    was never shared) has no other holders to protect, so it is the caller's to
    disconnect: ``True``.

    Not thread-safe: call it from the event loop thread.  It never awaits, so
    it cannot interleave with another task on that loop.
    """
    global global_slim, global_connection_id, global_slim_service
    global global_slim_insecure, global_slim_endpoint, global_slim_auth
    global global_slim_users

    if connection_id == global_connection_id:
        global_slim_users = max(global_slim_users - 1, 0)
        if global_slim_users > 0:
            return False
        global_slim = None
        global_slim_service = None
        global_connection_id = None
        global_slim_insecure = None
        global_slim_endpoint = None
        global_slim_auth = None
        return True

    retired = _retired_connection_users.get(connection_id)
    if retired is None:
        return True
    if retired > 1:
        _retired_connection_users[connection_id] = retired - 1
        return False
    del _retired_connection_users[connection_id]
    return True


def retire_slim_connection(connection_id: int | None = None) -> bool:
    """Stop handing out the cached connection; the next transport opens a new one.

    For a connection that is known to be broken.  Transports that already
    hold a share keep it, so nobody is cut off by someone else's report; the
    retired connection is disconnected when the last of them releases its
    share (see :func:`release_slim_connection`).

    With *connection_id*, only that connection is retired.  A late report
    about a connection that was already replaced does nothing, so it cannot
    retire the healthy replacement.  Without it, the current connection is
    retired.

    Returns ``True`` if a cached connection was retired.  Same threading rule
    as :func:`release_slim_connection`.
    """
    global global_slim, global_connection_id, global_slim_service
    global global_slim_insecure, global_slim_endpoint, global_slim_auth
    global global_slim_users

    if global_connection_id is None or (
        connection_id is not None and connection_id != global_connection_id
    ):
        return False
    if global_slim_users > 0:
        _retired_connection_users[global_connection_id] = global_slim_users
    global_slim = None
    global_slim_service = None
    global_connection_id = None
    global_slim_insecure = None
    global_slim_endpoint = None
    global_slim_auth = None
    global_slim_users = 0
    return True


def get_or_init_slim_service() -> slim_bindings.Service:
    """Initialise the SLIM runtime once and return the global ``Service``.

    Unlike :func:`get_or_create_slim_instance` this does **not** create an
    ``App`` or open a connection, so nothing gets subscribed to the SLIM
    dataplane.  Use it when the caller manages its own app and connection
    (the SlimRPC server does: a pub/sub app subscribed under the same name
    as the RPC server can receive the clients' RPC sessions, because SLIM
    delivers a session to only one of the subscribers of a name).
    """
    if not slim_bindings.is_initialized():
        tracing_config = slim_bindings.new_tracing_config()
        runtime_config = slim_bindings.new_runtime_config()
        service_config = slim_bindings.new_service_config()

        tracing_config.log_level = "info"

        slim_bindings.initialize_with_configs(
            tracing_config=tracing_config,
            runtime_config=runtime_config,
            service_config=[service_config],
        )

    return slim_bindings.get_global_service()


def new_client_config(endpoint: str, *, insecure: bool) -> slim_bindings.ClientConfig:
    """Connection config for the SLIM dataplane at *endpoint*.

    ``insecure=True`` connects in plaintext (no TLS).  ``insecure=False``
    uses TLS 1.3 with certificate verification against the system CA pool,
    so the dataplane must present a certificate trusted by this host.

    slim-bindings picks plaintext or TLS from the URL scheme, not from the
    TLS settings (verified against a live dataplane):

    * ``insecure=True`` is plaintext whatever the scheme; an ``https://`` URL
      only draws a warning, since the caller asked for plaintext explicitly.
    * ``insecure=False`` with an ``http://`` URL would silently connect in
      **plaintext**, so it is refused.  TLS needs an ``https://`` URL.

    Raises:
        ValueError: ``insecure=False`` was combined with an ``http://`` URL.
    """
    scheme = endpoint.split("://", 1)[0].lower() if "://" in endpoint else ""
    if not insecure and scheme == "http":
        raise ValueError(
            f"tls_insecure=False requires TLS, but the SLIM endpoint {endpoint!r} "
            "uses http://, which slim-bindings connects to in plaintext. Use an "
            "https:// endpoint, or set tls_insecure=True to accept plaintext."
        )
    if insecure and scheme == "https":
        logger.warning(
            "tls_insecure=True opens a plaintext connection, but the SLIM "
            "endpoint %r uses https. The connection will NOT use TLS.",
            endpoint,
        )
    if insecure:
        return slim_bindings.new_insecure_client_config(endpoint)
    return slim_bindings.new_secure_client_config(endpoint)


def _same_endpoint(a: str, b: str) -> bool:
    """True if two endpoint strings name the same dataplane.

    Only a trailing ``/`` is ignored (``http://h:1`` and ``http://h:1/`` are
    both used for the same server in this code base).  Everything else is
    compared as written, which is how slim-bindings keys connections too.
    """
    return a.rstrip("/") == b.rstrip("/")


_slim_instance_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _slim_instance_lock() -> asyncio.Lock:
    """The lock serialising shared-connection setup on the running loop.

    One lock per event loop: an ``asyncio.Lock`` binds to the loop it first
    waits on, and the shared connection outlives a single loop in tests.
    """
    loop = asyncio.get_running_loop()
    lock = _slim_instance_locks.get(loop)
    if lock is None:
        lock = _slim_instance_locks[loop] = asyncio.Lock()
    return lock


async def get_or_create_slim_instance(
    local: slim_bindings.Name,
    slim_endpoint: str,
    slim_insecure_client: bool,
    enable_opentelemetry: bool | None = None,
    shared_secret: str | None = None,
    jwt: str | None = None,
    bundle: str | None = None,
    audience: list[str] | None = None,
) -> tuple[slim_bindings.Service, slim_bindings.App, int]:
    """The process-wide SLIM connection, opened on first use.

    Each call takes one share of the connection.  Give it back with
    :func:`release_slim_connection` when done; the connection is disconnected
    only when the last share is released.
    """
    if enable_opentelemetry is not None:
        warnings.warn(
            "enable_opentelemetry is deprecated and ignored. SLIM is traced "
            "after Observe.init() when TRACING_ENABLED=true or "
            "AgntcyFactory(enable_tracing=True) is used.",
            DeprecationWarning,
            stacklevel=2,
        )
    # Without the lock, two setups awaited together both find no shared
    # connection, both connect, and the second silently replaces the first.
    async with _slim_instance_lock():
        return await _get_or_create_slim_instance(
            local,
            slim_endpoint,
            slim_insecure_client,
            shared_secret=shared_secret,
            jwt=jwt,
            bundle=bundle,
            audience=audience,
        )


async def _get_or_create_slim_instance(
    local: slim_bindings.Name,
    slim_endpoint: str,
    slim_insecure_client: bool,
    shared_secret: str | None = None,
    jwt: str | None = None,
    bundle: str | None = None,
    audience: list[str] | None = None,
) -> tuple[slim_bindings.Service, slim_bindings.App, int]:
    global global_slim, global_connection_id, global_slim_service
    global global_slim_insecure, global_slim_endpoint, global_slim_auth
    global global_slim_users

    if jwt or bundle:
        secret = None
        auth: tuple[Any, ...] = ("jwt", jwt, bundle, tuple(audience or ()))
    else:
        secret = resolve_shared_secret(shared_secret)
        auth = ("secret", hashlib.sha256(secret.encode()).hexdigest())

    # One connection is shared by every SLIMTransport in the process.  Reusing
    # it is fine, but only if it is the connection this caller asked for.
    #
    # WHY we refuse a mismatch instead of reusing silently: a transport that is
    # handed a connection to a different dataplane, one opened with the other
    # ``tls_insecure`` setting, or one authenticated with other credentials
    # (for example the public placeholder secret) does not fail, it just talks
    # to the wrong place or as the wrong identity, and the cause is invisible
    # from the caller's side.  Raising turns that into an immediate,
    # self-explanatory error at the point of the mistake, without changing how
    # the shared connection works for callers that are consistent.
    if (
        global_slim is not None
        and global_slim_service is not None
        and global_connection_id is not None
    ):
        if global_slim_endpoint is not None and not _same_endpoint(
            slim_endpoint, global_slim_endpoint
        ):
            raise ValueError(
                f"A SLIM connection is already open in this process to "
                f"{global_slim_endpoint!r}, but this transport asked for "
                f"{slim_endpoint!r}. All SLIMTransports in one process share a "
                "single connection and must use the same endpoint."
            )
        if global_slim_insecure is not None and (
            slim_insecure_client != global_slim_insecure
        ):
            raise ValueError(
                "A SLIM connection is already open in this process with "
                f"tls_insecure={global_slim_insecure}, but this transport asked "
                f"for tls_insecure={slim_insecure_client}. All SLIMTransports in "
                "one process share a single connection and must use the same "
                "tls_insecure setting."
            )
        if global_slim_auth is not None and auth != global_slim_auth:
            raise ValueError(
                "A SLIM connection is already open in this process with "
                "different credentials (shared secret or JWT) than this "
                "transport asked for. All SLIMTransports in one process share "
                "a single connection and must use the same credentials."
            )
        global_slim_users += 1
        return global_slim_service, global_slim, global_connection_id

    slim_service = get_or_init_slim_service()

    # Validate the connection settings before creating anything: a refused
    # ``tls_insecure`` / URL combination must not leave an App behind.
    client_config = new_client_config(slim_endpoint, insecure=slim_insecure_client)

    # Derive identity provider and verifier from JWK and JWT
    if secret is None:
        if not (jwt and bundle):
            raise ValueError(
                "JWT authentication needs both jwt and bundle; got only "
                f"{'jwt' if jwt else 'bundle'}."
            )
        provider, verifier = jwt_identity(
            jwt,
            bundle,
            local_name=str(local),
            aud=audience,
        )
    else:
        provider, verifier = shared_secret_identity(
            identity=str(local),
            secret=secret,
        )

    slim_app = slim_service.create_app(local, provider, verifier)

    slim_conn_id = await slim_service.connect_async(client_config)

    await slim_app.subscribe_async(local, slim_conn_id)

    global_slim = slim_app
    global_slim_service = slim_service
    global_connection_id = slim_conn_id
    global_slim_insecure = slim_insecure_client
    global_slim_endpoint = slim_endpoint
    global_slim_auth = auth
    global_slim_users = 1

    return slim_service, slim_app, slim_conn_id
