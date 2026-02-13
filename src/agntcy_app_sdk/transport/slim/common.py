# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import base64
import logging

import click
import datetime
import json
import slim_bindings


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
    global global_slim, global_connection_id, global_slim_service

    # # This check ensures that if global slim instances are already set
    if global_slim is not None and global_slim_service is not None:
        return global_slim_service, global_slim, global_connection_id

    # Initialize with config objects
    tracing_config = slim_bindings.new_tracing_config()
    runtime_config = slim_bindings.new_runtime_config()
    service_config = slim_bindings.new_service_config()

    tracing_config.log_level = "info"

    slim_bindings.initialize_with_configs(
        tracing_config=tracing_config,
        runtime_config=runtime_config,
        service_config=[service_config],
    )

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

    slim_service = slim_bindings.get_global_service()

    slim_app = slim_service.create_app(local, provider, verifier)

    if not slim_insecure_client:
        logging.warning("Only insecure client is supported at the moment.")

    client_config = slim_bindings.new_insecure_client_config(slim_endpoint)
    slim_conn_id = await slim_service.connect_async(client_config)

    await slim_app.subscribe_async(local, slim_conn_id)

    global_slim = slim_app
    global_slim_service = slim_service
    global_connection_id = slim_conn_id

    return slim_service, global_slim, global_connection_id
