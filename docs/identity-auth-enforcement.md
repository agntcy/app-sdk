# Identity auth enforcement across transports

## The issue

With identity auth turned on, the agent card told every client "send a bearer
token to call me", but only some transports checked it. The A2A card can only
state security for the whole agent, not per transport, so the claim covered
every way of reaching the agent:

| Transport            | Before this branch                                            |
| -------------------- | ------------------------------------------------------------- |
| SLIM / NATS patterns | Token sent and checked                                        |
| MCP (FastMCP)        | Token sent and checked                                        |
| HTTP JSON-RPC        | No token sent, nothing checked: anonymous calls were accepted |
| SlimRPC              | No token sent, nothing checked                                |

This was the same on `main` both before and after
[PR #126](https://github.com/agntcy/app-sdk/pull/126). #126 only moved the card
edit to the new a2a-sdk 1.x field format. So an operator who set
`IDENTITY_AUTH_ENABLED=true` believed the agent was protected, while its HTTP
endpoint was open to anyone. On top of that, the token was fetched with two
blocking gRPC calls on the event loop, for every request, with no caching.

## The expected behaviour

The A2A specification is clear on this:

- **Section 7.4:** a server "MUST authenticate every incoming request based on
  … its declared authentication requirements", with transport-appropriate
  errors (401/403 for HTTP).
- **Section 7.3:** clients send the credentials on every request.
- **Section 7.1:** production must use HTTPS, because a bearer token sent in
  clear can be stolen and replayed.

In short, whatever the card declares must be enforced on every transport it
lists. If a deployment does authentication somewhere else, such as an API
gateway or a service mesh, it should leave `IDENTITY_AUTH_ENABLED` off so the
card makes no claim.

## What we implemented

- **HTTP JSON-RPC server enforces the token.** `IdentityServiceMiddleware`
  returns 401 for a missing header and 403 for a token that fails
  verification. The agent card path stays public so clients can still
  discover the agent.
- **HTTP JSON-RPC client sends the token.** A token-adding interceptor
  attaches it automatically, only to servers whose card requires it, and
  leaves alone any `Authorization` the caller already set, in any letter
  case.
- **Patterns servers check before they listen.** The SLIM / NATS patterns
  handler sets up the Identity Service SDK before it subscribes, so a server
  that cannot check tokens fails at startup instead of serving requests.
- **Broadcast and group chat keep the caller's header and message.** They
  add the token to a copy of the message, and only when the caller set no
  `Authorization` of their own.
- **Tokens are fetched off the event loop and cached.**
  - The fetch runs in a worker thread, and each token is reused until 30 s
    before it expires, or for at most 5 minutes, whichever comes first.
  - It's cached per API key and tool name.
  - The two Identity Service calls share a 10 s gRPC deadline. A caller stops
    waiting after 10 s with a `TimeoutError`, and the next call starts a new
    fetch.
  - Concurrent cache misses share one fetch: every waiting request gets its
    token, or its error.
  - When a server rejects the token (401 / 403, or `UNAUTHENTICATED` /
    `PERMISSION_DENIED` on SlimRPC), every SDK client drops it from the
    cache, so the next call fetches a fresh one. A fetch that was abandoned,
    or that was running when the token was dropped, does not put its token
    back in the cache.
- **SlimRPC server enforces the token, and the client sends it.** No
  `slima2a` release forwards the call context (checked from 0.1.0 to 0.9.2),
  but its generated stub accepts call metadata and the server can read it.
  So the SDK's client uses an `SRPCTransport` subclass that sends the call's
  service parameters, the token among them, as metadata, and keeps
  `slima2a`'s client tracing spans. The SlimRPC server checks the token on
  every call before the agent sees it. A client built directly from `slima2a`
  sends no token and is rejected.
- **SlimRPC clients send the token to every peer.** A SlimRPC client has no
  served card to read; it is usually built from `minimal_agent_card(...)`,
  which declares no scheme. So with identity auth on, it sends the token on
  every call, whatever the card says. A peer that receives the token can
  replay it to other agents that accept it until it expires, so SlimRPC
  clients should only call peers they trust with it.
- **Plain `http://` is allowed by default, and can be refused.** The SDK
  backs demo apps whose agents run over plain HTTP, so the default
  (`IDENTITY_AUTH_ALLOW_HTTP=true`) keeps them working and logs only at debug
  level. With `IDENTITY_AUTH_ALLOW_HTTP=false`, the HTTP JSON-RPC client
  refuses to send a token to a plain `http://` URL on a non-local host.
  Section 7.1 asks for HTTPS in production; that is now one setting away. The
  setting does not cover SLIM, NATS, SlimRPC or MCP connections; those are
  secured with TLS on the SLIM / NATS endpoint.
- **Docs.** The usage guide and `docs/133-slim-2.2-check-decisions.md`
  document all of this, including the release note: HTTP and SlimRPC servers
  with auth enabled now reject clients without a valid token.

## What couldn't be implemented, or was left open

- **Refusing plain `http://` by default: chosen not to.** It would break the
  demo apps and agents behind a TLS-terminating proxy. The opt-in setting
  above covers deployments that want it.
- **Still open, not done:**
  - **The HTTP JSON-RPC server's check still blocks the event loop.** It uses
    the Identity Service SDK's own middleware, which verifies the token
    synchronously on the event loop with no deadline. While it waits, nothing
    else on that event loop runs (other SDK servers and clients in the same
    process included), and an Identity Service that never answers stalls them
    indefinitely. The FastMCP server's middleware does the same. Both log a
    warning about it at startup.
  - **The HTTP JSON-RPC 403 body carries the identity SDK's error text.** The
    same middleware puts the SDK's exception in the `reason` member of the
    403 body, which can name internal hosts. The patterns, SlimRPC and MCP
    replies are generic and log the detail instead.
  - **The identity SDK is imported at module load.**
    `src/agntcy_app_sdk/common/auth.py` imports `IdentityServiceSdk` at the
    top of the module, so the import cost is paid even when auth is off.
