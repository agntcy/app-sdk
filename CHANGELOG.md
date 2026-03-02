# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.3](https://github.com/agntcy/app-sdk/compare/v0.5.1...v0.5.3) (2026-03-02)


### Features

* Agntcy dir client integration and enhanced CI pipeline ([#112](https://github.com/agntcy/app-sdk/issues/112)) ([5d8fba3](https://github.com/agntcy/app-sdk/commit/5d8fba385eda6ada9c6068f3c8e51f09b6b64ff5))


### Bug Fixes

* **ci:** use correct secret name for release-please token ([#116](https://github.com/agntcy/app-sdk/issues/116)) ([3c7f120](https://github.com/agntcy/app-sdk/commit/3c7f120986206adf85fcd3fe01a75566bb61044b))


### Chores

* bump agntcy-dir dependency from >=0.6.1 to >=1.0.0 ([#113](https://github.com/agntcy/app-sdk/issues/113)) ([85b7f02](https://github.com/agntcy/app-sdk/commit/85b7f02ddc8bc2409792290496fc0ca0878b2ed7))
* bump package version to 0.5.2 ([#115](https://github.com/agntcy/app-sdk/issues/115)) ([16279ba](https://github.com/agntcy/app-sdk/commit/16279baa838011c74d3ee9e315c190a4a57a3806))

## 0.5.0

### Added
* `SlimRpcConnectionConfig` dataclass — typed SLIM connectivity parameters (`identity`, `shared_secret`, `endpoint`, `tls_insecure`) replacing the untyped `dict[str, Any]` that was previously passed via `slimrpc_server_config`
* `A2ASlimRpcServerConfig` — renamed from `A2ASRPCConfig` for clarity; now accepts a typed `connection: SlimRpcConnectionConfig` field instead of `slimrpc_server_config: dict`
* `SlimRpcConnectionConfig` exported from `agntcy_app_sdk.semantic.a2a.server` and `agntcy_app_sdk.semantic.a2a`
* Native HTTP JSONRPC serving — omit `.with_transport()` on an `A2AStarletteApplication` to serve over HTTP via `A2AJsonRpcServerHandler`
* `A2AClientFactory` with card-driven transport negotiation (`factory.a2a(config).create(card)`)
* `A2AClientFactory.connect()` convenience method for HTTP JSONRPC clients
* `ClientConfig` with multi-transport support (`slimrpc`, `slimpatterns`, `natspatterns`, `jsonrpc`)
* `A2AExperimentalClient` subclass with real `broadcast_message()`, `start_groupchat()`, and streaming variants
* Fluent `ContainerBuilder` API (`session.add(target).with_transport(...).build()`)
* `A2AJsonRpcServerHandler` for serving A2A over HTTP without a transport

### Changed
* **Breaking:** `A2ASRPCConfig` renamed to `A2ASlimRpcServerConfig` (no deprecated alias)
* **Breaking:** `A2ASlimRpcServerConfig.connection` (typed `SlimRpcConnectionConfig`) replaces `A2ASRPCConfig.slimrpc_server_config` (untyped `dict[str, Any]`)
* **Breaking:** `factory.create_client()` and `factory.create_protocol()` removed — use `factory.a2a()`, `factory.mcp()`, `factory.fast_mcp()` instead
* **Breaking:** `AppContainer` constructor reworked — use `ContainerBuilder` fluent API via `session.add(target)`
* **Breaking:** `push_to_directory_on_startup` parameter removed from `AppContainer.run()`, `start_session()`, and `start_all_sessions()` — directory push is now automatic
* **Breaking:** Enum classes `ProtocolTypes`, `TransportTypes`, `ObservabilityProviders`, `IdentityProviders` removed from `agntcy_app_sdk.factory`
* **Breaking:** `factory.create_transport()` now raises `ValueError` on unknown transport type (previously returned `None`)
* **Breaking:** `A2AProtocol` removed — topic generation moved to `A2AExperimentalServer.create_agent_topic()`
* **Breaking:** `create_agent_topic()` now replaces spaces with underscores in topic names
* Handler auto-detection in `ContainerBuilder.build()` selects handler class from target type
* `preferred_transport` automatically stamped on agent card during server setup
* Topic encoded in `card.url` (e.g., `slim://...`) for client-side transport discovery

### Removed
* `A2ASRPCConfig` (replaced by `A2ASlimRpcServerConfig`)
* `slimrpc_server_config` dict field (replaced by typed `connection: SlimRpcConnectionConfig`)
* `fallback_slim_endpoint` module-level constant (default now lives on `SlimRpcConnectionConfig.endpoint`)
* `__post_init__` validation on the server config (dataclass constructor enforces required fields)
* `factory.create_client()`, `factory.create_protocol()`
* `A2AProtocol`, `BaseAgentProtocol`
* `ProtocolTypes`, `TransportTypes`, `ObservabilityProviders`, `IdentityProviders` enums

## 0.4.0

### Added
* Pubsub gather streaming
* Groupchat streaming
* App Session management
* Directory abstract class

### Changed
* Refactored directory structure to match agntcy tech pillars
* protocols directory renamed to semantic
* transports directory renamed to transport
* message bridge replaced with app containers and app sessions


### Fixed

## 0.2.3

### Added
- SLIM multi-session lifecycle management
- SLIM groupchat sessions, initiated with A2AClient.broadcast_message(group_chat=True)

### Changed
- AgntcyFactory.create_transport requires a name field when the type is SLIM, in the form /org/namespace/service
- A2AClient.broadcast_message, when created from factory, requires list of recipients to fulfill SLIM requirements

### Fixed
