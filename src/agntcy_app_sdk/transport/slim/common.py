# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import weakref

import click
import datetime
import json
import slim_bindings

from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)


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
def shared_secret_identity(identity, secret):
    """
    Create a provider and verifier using a shared secret.
    """
    provider = slim_bindings.IdentityProviderConfig.SHARED_SECRET(
        id=identity, data=secret
    )
    verifier = slim_bindings.IdentityVerifierConfig.SHARED_SECRET(
        id=identity, data=secret
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
    iss: str = None,
    sub: str = None,
    aud: list = None,
):
    """
    Parse the JWK and JWT from the provided strings.
    """

    with open(jwk_path) as jwk_file:
        jwk_string = jwk_file.read()

    # The JWK is normally encoded as base64, so we need to decode it
    spire_jwks = json.loads(jwk_string)

    for _, v in spire_jwks.items():
        # Decode first item from base64
        spire_jwks = base64.b64decode(v)
        break

    # Read the static JWT file for signing
    with open(jwt_path) as jwt_file:
        jwt_content = jwt_file.read()

    # Create encoding key config for JWT signing
    encoding_key_config = slim_bindings.JwtKeyConfig(
        algorithm=slim_bindings.JwtAlgorithm.RS256,
        format=slim_bindings.JwtKeyFormat.PEM,
        key=slim_bindings.JwtKeyData.DATA(value=jwt_content),
    )

    # Create provider config for JWT authentication
    provider_config = slim_bindings.IdentityProviderConfig.JWT(
        config=slim_bindings.ClientJwtAuth(
            key=slim_bindings.JwtKeyType.ENCODING(key=encoding_key_config),
            audience=aud or ["default-audience"],
            issuer=iss or "default-issuer",
            subject=sub or local_name,
            duration=datetime.timedelta(seconds=3600),
        )
    )

    # Create decoding key config for JWKS verification
    decoding_key_config = slim_bindings.JwtKeyConfig(
        algorithm=slim_bindings.JwtAlgorithm.RS256,
        format=slim_bindings.JwtKeyFormat.JWKS,
        key=slim_bindings.JwtKeyData.DATA(value=spire_jwks),
    )

    # Create verifier config
    verifier_config = slim_bindings.IdentityVerifierConfig.JWT(
        config=slim_bindings.JwtAuth(
            key=slim_bindings.JwtKeyType.DECODING(key=decoding_key_config),
            audience=aud or ["default-audience"],
            issuer=iss or "default-issuer",
            subject=sub,
            duration=datetime.timedelta(seconds=3600),
        )
    )

    return provider_config, verifier_config


# A custom click parameter type for parsing dictionaries from JSON strings
# This is useful for passing complex configurations via command line arguments
class DictParamType(click.ParamType):
    name = "dict"

    def convert(self, value, param, ctx):
        import json

        if isinstance(value, dict):
            return value  # Already a dict (for default value)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            self.fail(f"{value} is not valid JSON", param, ctx)


global_slim = None
global_connection_id = None
global_slim_service = None
# ``insecure`` flag the cached connection above was opened with.
global_slim_insecure: bool | None = None
global_slim_endpoint: str | None = None


def get_or_init_slim_service() -> slim_bindings.Service:
    """Initialise the SLIM runtime once and return the global ``Service``.

    Unlike :func:`get_or_create_slim_instance` this does **not** create an
    ``App`` or open a connection, so nothing gets subscribed to the SLIM
    dataplane.  Use it when the caller manages its own app and connection
    (the SlimRPC server does: a pub/sub app subscribed under the same name
    as the RPC server would receive the clients' RPC sessions, which SLIM
    2.x routes to the first matching subscriber).
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
    enable_opentelemetry: bool = False,
    shared_secret: str | None = None,
    jwt: str | None = None,
    bundle: str | None = None,
    audience: list[str] | None = None,
):
    # Without the lock, two setups awaited together both find no shared
    # connection, both connect, and the second silently replaces the first.
    async with _slim_instance_lock():
        return await _get_or_create_slim_instance(
            local,
            slim_endpoint,
            slim_insecure_client,
            enable_opentelemetry=enable_opentelemetry,
            shared_secret=shared_secret,
            jwt=jwt,
            bundle=bundle,
            audience=audience,
        )


async def _get_or_create_slim_instance(
    local: slim_bindings.Name,
    slim_endpoint: str,
    slim_insecure_client: bool,
    enable_opentelemetry: bool = False,
    shared_secret: str | None = None,
    jwt: str | None = None,
    bundle: str | None = None,
    audience: list[str] | None = None,
):
    global global_slim, global_connection_id, global_slim_service
    global global_slim_insecure, global_slim_endpoint

    # One connection is shared by every SLIMTransport in the process.  Reusing
    # it is fine, but only if it is the connection this caller asked for.
    #
    # WHY we refuse a mismatch instead of reusing silently: a transport that is
    # handed a connection to a different dataplane (or one opened with the other
    # ``tls_insecure`` setting) does not fail, it just talks to the wrong place,
    # and the cause is invisible from the caller's side.  Raising turns that
    # into an immediate, self-explanatory error at the point of the mistake,
    # without changing how the shared connection works for callers that are
    # consistent.
    if global_slim is not None and global_slim_service is not None:
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
        return global_slim_service, global_slim, global_connection_id

    slim_service = get_or_init_slim_service()

    # Validate the connection settings before creating anything: a refused
    # ``tls_insecure`` / URL combination must not leave an App behind.
    client_config = new_client_config(slim_endpoint, insecure=slim_insecure_client)

    if not jwt and not bundle:
        if not shared_secret:
            raise ValueError(
                "Either JWT or bundle must be provided, or a shared secret."
            )

    # Derive identity provider and verifier from JWK and JWT
    if jwt and bundle:
        provider, verifier = jwt_identity(
            jwt,
            bundle,
            local_name=str(local),
            aud=audience,
        )
    else:
        provider, verifier = shared_secret_identity(
            identity=str(local),
            secret=shared_secret,
        )

    slim_app = slim_service.create_app(local, provider, verifier)

    slim_conn_id = await slim_service.connect_async(client_config)

    await slim_app.subscribe_async(local, slim_conn_id)

    global_slim = slim_app
    global_slim_service = slim_service
    global_connection_id = slim_conn_id
    global_slim_insecure = slim_insecure_client
    global_slim_endpoint = slim_endpoint

    return slim_service, global_slim, global_connection_id
