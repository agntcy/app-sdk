# Test Coverage Report

> Last updated: 2026-10-06 (SLIM 2.2.0, slim-bindings 2.2, a2a-sdk 1.1.0)

## Overview

| Category  |   Tests |  Passed | Failed | Skipped |
| --------- | ------: | ------: | -----: | ------: |
| **Unit**  |     322 |     322 |      0 |       0 |
| **E2E**   |      60 |      48 |      0 |      12 |
| **Total** | **382** | **370** |  **0** |  **12** |

E2E figures cover every E2E file, including `test_directory.py` (9 tests, run
against `dir-api-server` v1.0.0 and `zot`).
The 12 skips are all by design ("broadcast not applicable to JSONRPC", "group
chat not applicable to NATS", "MCP not applicable to JSONRPC", and similar).

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
| `test_card_bootstrap.py`          |    81 | URL parsing (`slim://`, `nats://`, `http://`), transport aliasing, dry-run plan generation, `CardBuilder` container building, `InterfaceTransport` constants, `AppSession.add_a2a_card()` delegation, `.override()` / `.skip()` / fluent chaining, warning when `slim` and `slimrpc` share one SLIM name |
| `test_a2a_client_config.py`       |   112 | `ClientConfig` post-init logic (incl. deprecated `supported_transports`), transport config validation, `_parse_topic_from_url`, `PatternsClientTransport` adapter, `A2AExperimentalClient` API and `ClientCallInterceptor` integration, `A2AClientFactory` negotiation over `supported_interfaces` (server/client preference, multi-transport), dispatch to slimrpc/slimpatterns/natspatterns/jsonrpc |
| `test_a2a_server.py`              |    45 | `A2AServerConfig` and deprecated `A2AStarletteApplication` shim, HTTP JSON-RPC app, interface declaration by server handlers, patterns bridge over an in-memory loopback transport (unary, streaming, errors, relay, identity auth), `with_public_url` / JSONRPC interface declaration |
| `test_card_utils.py`              |    18 | `supported_interfaces` helpers: `add_interface` / `find_interface` / `clone_card`, binding casing and aliases |
| `test_get_agent_identifier.py`    |    21 | Topic/identifier derivation from the agent card |
| `test_agent_directory.py`         |    12 | `AgentDirectory` setup, push (AgentCard, raw dict), pull (by CID, extract card), search, teardown, error handling                                                                                                                                                                       |
| `test_factory.py`                 |    10 | `AgntcyFactory` construction, transport creation, protocol accessor registration, directory creation, re-exports                                                                                                                                                                        |
| `test_nats_invite_protocol.py`    |     9 | NATS invite/ACK subscribe flow, teardown unsubscribe, message handler intercept (invite, teardown, normal), `gather_stream` single/multi recipient, ephemeral subscription cleanup                                                                                                      |
| `test_oasf_converter.py`          |     8 | AgentCard to OASF conversion (with/without provider), OASF to AgentCard roundtrip, edge cases (no matching module, empty modules)                                                                                                                                                       |
| `test_app_container_directory.py` |     5 | `AppContainer` integration with directory (push record, no record, no directory), stop/teardown                                                                                                                                                                                         |
| `test_app_session.py`             |     1 | `AppSession` basic lifecycle                                                                                                                                                                                                                                                            |

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
