# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import click
import slim_bindings


# Split an ID into its components
# Expected format: organization/namespace/application
# Raises ValueError if the format is incorrect
# Returns a PyName with the 3 components
def split_id(id):
    try:
        organization, namespace, app = id.split("/")
    except ValueError as e:
        raise e

    return slim_bindings.PyName(organization, namespace, app)


# Create a shared secret identity provider and verifier
# This is used for shared secret authentication
# Takes an identity and a shared secret as parameters
# Returns a tuple of (provider, verifier)
# This is used for shared secret authentication
def shared_secret_identity(identity, secret):
    """
    Create a provider and verifier using a shared secret.
    """
    provider = slim_bindings.PyIdentityProvider.SharedSecret(
        identity=identity, shared_secret=secret
    )
    verifier = slim_bindings.PyIdentityVerifier.SharedSecret(
        identity=identity, shared_secret=secret
    )

    return provider, verifier


# Create a JWT identity provider and verifier
# This is used for JWT authentication
# Takes private key path, public key path, and algorithm as parameters
# Returns a Slim object with the provider and verifier
def jwt_identity(
    jwt_path: str,
    jwk_path: str,
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

    provider = slim_bindings.PyIdentityProvider.StaticJwt(
        path=jwt_path,
    )

    pykey = slim_bindings.PyKey(
        algorithm=slim_bindings.PyAlgorithm.RS256,
        format=slim_bindings.PyKeyFormat.Jwks,
        key=slim_bindings.PyKeyData.Content(content=spire_jwks.decode("utf-8")),
    )

    verifier = slim_bindings.PyIdentityVerifier.Jwt(
        public_key=pykey,
        issuer=iss,
        audience=aud,
        subject=sub,
    )

    return provider, verifier


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


async def create_local_app(
    local: slim_bindings.PyName,
    slim: dict,
    remote: str | None = None,
    enable_opentelemetry: bool = False,
    shared_secret: str | None = None,
    jwt: str | None = None,
    bundle: str | None = None,
    audience: list[str] | None = None,
):
    # init tracing
    slim_bindings.init_tracing(
        {
            "log_level": "info",
            "opentelemetry": {
                "enabled": enable_opentelemetry,
                "grpc": {
                    "endpoint": "http://localhost:4317",
                },
            },
        }
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
            aud=audience,
        )
    else:
        provider, verifier = shared_secret_identity(
            identity=str(local),
            secret=shared_secret,
        )
    # TODO: leverage global slim object after this bug is fixed: https://github.com/agntcy/slim/issues/876
    # local_app = await slim_bindings.Slim.new(local, provider, verifier)
    
    local_app = await slim_bindings.Slim.new(local, provider, verifier, local_service=True)
    # Connect to slim server
    _ = await local_app.connect(slim)

    return local_app
