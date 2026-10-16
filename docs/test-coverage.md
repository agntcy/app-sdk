# Test Coverage Report

> Last updated: 2026-10-16 (SLIM 2.2.0, slim-bindings 2.2, a2a-sdk 1.1.0)

## Overview

| Category  | Tests | Passed | Failed | Skipped |
| --------- | ----: | -----: | -----: | ------: |
| **Unit**  |   590 |    590 |      0 |       0 |
| **E2E**   |    68 |      — |      — |       — |

The E2E count is what pytest collects across every E2E file, including
`test_directory.py` (9 tests, run against `dir-api-server` v1.0.0 and `zot`)
and `test_slim_tls.py` (7 tests, against a TLS dataplane it starts itself).
Pass/skip figures are per file in the sections below. Skips are by design
("broadcast not applicable to JSONRPC", "group chat not applicable to NATS",
"MCP not applicable to JSONRPC", and similar).

---

## Unit Tests

Unit tests run in-process with no external dependencies (Docker, SLIM, NATS).
All transport and protocol interactions are mocked.

```
uv run pytest tests/unit/ -v
```

### By Module

| File                              | Tests | What It Covers                                                                                                                                                                                                                                                                          |
| --------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_a2a_client_config.py`       |   160 | `ClientConfig` post-init logic (incl. deprecated `supported_transports`, also assigned after construction), transport config validation, `_parse_topic_from_url`, `PatternsClientTransport` adapter, `A2AExperimentalClient` API and `ClientCallInterceptor` integration, broadcast failures (`A2ABroadcastError`), `A2AClientFactory` negotiation over `supported_interfaces` (server/client preference, multi-transport, protocol version), dispatch to slimrpc/slimpatterns/natspatterns/jsonrpc, slimrpc and patterns transport producers (missing `slimrpc_channel_factory`, upstream `ClientConfig` rejected) |
| `test_card_bootstrap.py`          |   124 | URL parsing (`slim://`, `nats://`, `http://`, `https://`, trailing `/`, IPv6), transport aliasing, dry-run plan generation, `CardBuilder` container building, `InterfaceTransport` constants, `AppSession.add_a2a_card()` delegation, `.override()` / `.skip()` / fluent chaining (patterns overrides that are not a `BaseTransport` rejected in `start()` and `dry_run()`), warning when `slim` and `slimrpc` share one SLIM name (including `slim://org/ns/agent`), `.with_public_url()` (bind vs advertised URL), `.with_slim_tls()` (including that `.override()` is left as given) and `https://` SLIM endpoints |
| `test_a2a_server.py`              |    65 | `A2AServerConfig` and deprecated `A2AStarletteApplication` shim, HTTP JSON-RPC app, interface declaration by server handlers, default topic from a pub/sub interface only (card order when the transport is unknown), patterns bridge over an in-memory loopback transport (unary, streaming, errors, relay, identity auth, sender header), identity auth without a configured identity service denies, `with_public_url` / JSONRPC interface declaration |
| `test_slim_tls.py`                |    34 | `tls_insecure` reaches every SLIM connection (transport, SlimRPC server and client), TLS over `http://` refused, shared SLIM connection refuses a different endpoint or `tls_insecure` (also for setups awaited concurrently) |
| `test_slim_broadcast_timeout.py`  |     5 | SLIM broadcast and group chat raise `TimeoutError` (not `UnboundLocalError` or `CancelledError`) when setup stalls, no reply arrives, or the consumer is slow |
| `test_card_utils.py`              |    18 | `supported_interfaces` helpers: `add_interface` / `find_interface` / `clone_card`, binding casing and aliases |
| `test_get_agent_identifier.py`    |    46 | Topic/identifier derivation from the agent card; without an interface type, the first interface in card order that has an identifier (bare `org/ns/name` slimrpc identities included); SLIM `http(s)://` URLs, matching `parse_interface_url` |
| `test_agent_directory.py`         |    12 | `AgentDirectory` setup, push (AgentCard, raw dict), pull (by CID, extract card), search, teardown, error handling                                                                                                                                                                       |
| `test_factory.py`                 |    10 | `AgntcyFactory` construction, transport creation, protocol accessor registration, directory creation, re-exports                                                                                                                                                                        |
| `test_nats_invite_protocol.py`    |    19 | NATS invite/ACK subscribe flow, teardown unsubscribe, message handler intercept (invite, teardown, normal), one-way messages get no reply, one error reply for an empty response, `publish_fn` passed only to handlers that accept it (a `functools.wraps` wrapper is judged by its own signature), a `TypeError` inside a handler runs it once, `gather_stream` single/multi recipient and teardown when the caller stops early or publishing fails, ephemeral subscription cleanup |
| `test_nats_transport.py`          |     2 | `setup()` with a disconnected client and no endpoint raises, `reconnected_cb` without a connected URL does not raise |
| `test_slim_transport_init.py`     |    13 | `SLIMTransport.from_config` name check, injected SLIM app (connection id required, registered with the session manager), request timeout closes the session, receive loop stops on each "session gone" error and retries unknown errors, callback runs once with `publish_fn` only when accepted (including a `functools.wraps` wrapper) |
| `test_accepts_publish_fn.py`      |    10 | `accepts_publish_fn` for plain, keyword, keyword-only and `**kwargs` handlers, bound methods, callable objects, `functools.partial`, `functools.wraps` wrappers and `AsyncMock` |
| `test_slim_jwt_identity.py`       |     7 | JWT/JWKS identity (decoding key, empty JWK file, incomplete identity rejected), warning when the placeholder shared secret is used |
| `test_mcp_protocol.py`            |     8 | MCP over a loopback transport (initialize then call a tool, server notifications do not stop the server), `handle_message` rejects notifications, topic required by the client factory and server handler, `setup()` before binding a server, `message_translator` not implemented |
| `test_fast_mcp_protocol.py`       |     5 | FastMCP ASGI `receive()` reports a disconnect only after the response, topic required with a transport (client, server handler, call), `setup()` before binding a server |
| `test_transport_streaming_defaults.py` | 5 | Unsupported streaming methods raise `NotImplementedError` on iteration (base mixins, NATS streaming conversation), and the patterns client transport falls back on them |
| `test_streamable_http_transport.py` |   1 | `from_client` is not supported |
| `test_message.py`                 |     3 | `Message` payload: `str` stored as UTF-8 bytes, `bytes` unchanged, round trip |
| `test_tracing_init.py`            |    28 | Tracing instrumentor setup: nothing when not requested, warn once without `ioa_observe` init, instrument once across transports, pending instrumentors installed after init, failing or unimportable instrumentors logged and not retried, factory request kept in memory (not inherited by child processes), deprecated `enable_opentelemetry` / `tracing_enabled`, import does not load `ioa_observe` |
| `test_oasf_converter.py`          |     8 | AgentCard to OASF conversion (with/without provider), OASF to AgentCard roundtrip, edge cases (no matching module, empty modules)                                                                                                                                                       |
| `test_app_container_directory.py` |     5 | `AppContainer` integration with directory (push record, no record, no directory), stop/teardown                                                                                                                                                                                         |
| `test_app_session.py`             |     2 | `AppSession` basic lifecycle, a shutdown signal before `run()` raises a clear `RuntimeError`                                                                                                                                                                                            |

---

## E2E Tests

E2E tests run against real Docker services and exercise the full stack:
transport connections, message serialization, protocol handlers, and server lifecycle.

**Prerequisites:**

```bash
docker-compose -f services/docker/docker-compose.yaml up
```

The SLIM and NATS tests only need two services (SLIM **2.2.0** and NATS); start just those if
other containers already use the remaining ports:

```bash
docker compose -f services/docker/docker-compose.yaml up -d nats slim
```

Run each E2E file on its own (as `task test-e2e` does), not all at once in a single pytest run.
SLIM logs `connection unknown` ERROR lines between tests; these are harmless.

| Service        | Port     | Required By           |
| -------------- | -------- | --------------------- |
| SLIM dataplane | `:46357` | SLIM, SLIMRPC tests   |
| NATS server    | `:4222`  | NATS tests            |
| ClickHouse     | `:8123`  | Tracing/observability |
| OTEL Collector | `:4317`  | Tracing/observability |

### test_card_bootstrap.py

`add_a2a_card()` card-driven bootstrap. The agent card is the single source of truth --
`add_a2a_card()` reads `supported_interfaces` and starts all transports automatically.

```
uv run pytest tests/e2e/test_card_bootstrap.py -s -v
```

| Test                      | Transport     | Status | What It Proves                                                                                                           |
| ------------------------- | ------------- | :----: | ------------------------------------------------------------------------------------------------------------------------ |
| `test_client[NATS]`       | NATS patterns |  PASS  | Point-to-point A2A over NATS via `add_a2a_card()`                                                                        |
| `test_client[SLIM]`       | SLIM patterns |  PASS  | Point-to-point A2A over SLIM via `add_a2a_card()`                                                                        |
| `test_client[JSONRPC]`    | HTTP JSON-RPC |  PASS  | Point-to-point A2A over HTTP via `add_a2a_card()`                                                                        |
| `test_client[SLIMRPC]`    | SLIM RPC      |  PASS  | Point-to-point A2A over SlimRPC via `add_a2a_card()` -- proves slimrpc + slimpatterns coexist on the same server process |
| `test_broadcast[NATS]`    | NATS patterns |  PASS  | Fan-out broadcast to 3 agents over NATS                                                                                  |
| `test_broadcast[SLIM]`    | SLIM patterns |  PASS  | Fan-out broadcast to 3 agents over SLIM                                                                                  |
| `test_broadcast[JSONRPC]` | HTTP JSON-RPC |  SKIP  | Broadcast not applicable for HTTP                                                                                        |
| `test_broadcast[SLIMRPC]` | SLIM RPC      |  SKIP  | SlimRPC is point-to-point only                                                                                           |
| `test_dry_run`            | All           |  PASS  | `add_a2a_card().dry_run()` returns correct plan without starting anything                                                |
| `test_dry_run_with_skip`  | All           |  PASS  | `add_a2a_card().skip("jsonrpc").dry_run()` omits skipped transport from plan                                             |
| `test_public_url_is_advertised_while_the_server_binds_locally` | HTTP JSON-RPC |  PASS  | `.with_public_url()`: server binds to the card URL, served card advertises the public URL (no Docker) |

### test_a2a_slimrpc.py

Standalone SlimRPC tests using the manual builder API (`session.add(config).build()`).

```
uv run pytest tests/e2e/test_a2a_slimrpc.py -s -v
```

| Test                           | Status | What It Proves                                               |
| ------------------------------ | :----: | ------------------------------------------------------------ |
| `test_client`                  |  PASS  | Basic SlimRPC point-to-point with manual server setup        |
| `test_client_factory`          |  PASS  | SlimRPC via `A2AClientFactory` with eager channel factory    |
| `test_client_factory_deferred` |  PASS  | SlimRPC via `A2AClientFactory` with deferred `SlimRpcConfig` |

### test_a2a_starlette.py

A2A Starlette server using the manual builder API with per-transport parametrization.

```
uv run pytest tests/e2e/test_a2a_starlette.py -s -v
```

| Test                       | NATS | SLIM | JSONRPC | What It Proves                      |
| -------------------------- | :--: | :--: | :-----: | ----------------------------------- |
| `test_client`              | PASS | PASS |  PASS   | Point-to-point A2A request/response |
| `test_broadcast`           | PASS | PASS |  SKIP   | Fan-out to 3 agents                 |
| `test_broadcast_streaming` | PASS | PASS |  SKIP   | Streaming fan-out to 3 agents       |
| `test_groupchat`           | SKIP | PASS |  SKIP   | Multi-agent group chat (SLIM only)  |
| `test_groupchat_streaming` | SKIP | PASS |  SKIP   | Streaming group chat (SLIM only)    |

### test_mcp.py

MCP (Model Context Protocol) server over transports.

```
uv run pytest tests/e2e/test_mcp.py -s -v
```

| Test          | NATS | SLIM | JSONRPC |
| ------------- | :--: | :--: | :-----: |
| `test_client` | PASS | PASS |  SKIP   |

### test_fast_mcp.py

FastMCP server over transports.

```
uv run pytest tests/e2e/test_fast_mcp.py -s -v
```

| Test          | NATS | SLIM | JSONRPC |
| ------------- | :--: | :--: | :-----: |
| `test_client` | PASS | PASS |  SKIP   |

### test_concurrent_fast_mcp.py

Concurrent FastMCP requests over transports.

```
uv run pytest tests/e2e/test_concurrent_fast_mcp.py -s -v
```

| Test          | NATS | SLIM | JSONRPC |
| ------------- | :--: | :--: | :-----: |
| `test_client` | PASS | PASS |  SKIP   |

### test_a2a_usage_guide.py

End-to-end examples from the A2A usage guide.

```
uv run pytest tests/e2e/test_a2a_usage_guide.py -s -v
```

| Test                               | Status | Notes                         |
| ---------------------------------- | :----: | ----------------------------- |
| `test_weather_agent_srpc`          |  PASS  | SlimRPC weather agent example |
| `test_weather_agent_slim_patterns` | FAIL\* | Pre-existing failure          |
| `test_weather_agent_nats_patterns` | FAIL\* | Pre-existing failure          |

\*These failures are pre-existing and unrelated to the slimrpc/slimpatterns coexistence fix.

### test_directory.py

Agent directory push/pull/search operations (requires the `dir-api-server` Docker service on `127.0.0.1:8888`, with its `postgres` and `zot` dependencies running).

```
uv run pytest tests/e2e/test_directory.py -s -v
```

| Test                                            | What It Proves                             |
| ----------------------------------------------- | ------------------------------------------ |
| `test_push_and_pull_agent_card`                 | Push an AgentCard, pull it back by CID     |
| `test_push_and_pull_extract_card`               | Push and extract card fields               |
| `test_push_raw_oasf_dict`                       | Push raw OASF dict format                  |
| `test_search_by_name`                           | Search directory by agent name             |
| `test_push_pull_search_roundtrip`               | Full push/pull/search lifecycle            |
| `test_app_container_pushes_record_to_directory` | AppContainer auto-publishes to directory   |
| `test_app_session_start_all_pushes_records`     | AppSession batch publishes on start        |
| `test_app_container_no_directory_skips_push`    | Graceful skip when no directory configured |
| `test_factory_create_directory_in_pipeline`     | Factory creates directory in pipeline      |

### test_slim_tls.py

`tls_insecure` against a live TLS dataplane. The test starts its own SLIM
container with a certificate generated for the run (test-only, never stored in
the repo), so it needs Docker but not the compose stack.

```
uv run pytest tests/e2e/test_slim_tls.py -s -v
```

| Test                                                                     | What It Proves                                                       |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `TestTlsConnectionMatrix::test_untrusted_certificate_is_rejected`        | `tls_insecure=False` refuses a certificate the host doesn't trust    |
| `TestTlsConnectionMatrix::test_trusted_certificate_connects`             | `tls_insecure=False` connects with a trusted certificate             |
| `TestTlsConnectionMatrix::test_insecure_flag_is_plaintext_even_for_https_url` | `tls_insecure=True` is plaintext, even for `https://`           |
| `TestTlsConnectionMatrix::test_tls_over_http_url_is_refused_before_connecting` | TLS over `http://` raises before dialling                      |
| `TestTlsConnectionMatrix::test_canary_slim_bindings_still_goes_plaintext_over_http` | Fails if slim-bindings stops going plaintext over `http://` |
| `test_slim_transport_connects_over_tls_and_guards_the_shared_connection` | `SLIMTransport` over TLS; the shared-connection guard holds          |
| `test_slimrpc_round_trip_over_tls`                                       | SlimRPC request/response over TLS                                    |

---

## Transport Coverage Matrix

Which transports are tested by which E2E suites:

| Transport          |  card_bootstrap   |                               starlette                                |              slimrpc              |  mcp   | fast_mcp | concurrent_mcp | usage_guide |
| ------------------ | :---------------: | :--------------------------------------------------------------------: | :-------------------------------: | :----: | :------: | :------------: | :---------: |
| **SLIM patterns**  | client, broadcast | client, broadcast, broadcast_streaming, groupchat, groupchat_streaming |                --                 | client |  client  |     client     |   FAIL\*    |
| **NATS patterns**  | client, broadcast |                 client, broadcast, broadcast_streaming                 |                --                 | client |  client  |     client     |   FAIL\*    |
| **JSONRPC (HTTP)** |      client       |                                 client                                 |                --                 |   --   |    --    |       --       |     --      |
| **SlimRPC**        |      client       |                                   --                                   | client, factory, factory_deferred |   --   |    --    |       --       |   client    |

---

## Running All Tests

```bash
# Unit tests (no Docker needed)
uv run pytest tests/unit/ -v

# All E2E tests (Docker services must be running)
uv run pytest tests/e2e/ -s -v

# Single transport filter
uv run pytest tests/e2e/test_card_bootstrap.py -s -k "SLIMRPC"

# Lint
pre-commit run --all-files
```
