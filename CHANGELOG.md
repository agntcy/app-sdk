# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.3](https://github.com/agntcy/app-sdk/compare/v0.5.2...v0.5.3) (2026-03-02)


### Features

* Add Trace Instrumentation for NATS Transport ([#33](https://github.com/agntcy/app-sdk/issues/33)) ([96b77e8](https://github.com/agntcy/app-sdk/commit/96b77e895b29959b4528ec31ef9e5d38df5ac730))
* adding a2a _send_request span ([204618e](https://github.com/agntcy/app-sdk/commit/204618e8adc23fe41a9e28c84d88f803e722df4f))
* adding A2A card resolution via pubsub topic option ([a376679](https://github.com/agntcy/app-sdk/commit/a37667980cb600fc19a33fde64f7f4f7850b6ea4))
* adding baseTransport class ([65145c4](https://github.com/agntcy/app-sdk/commit/65145c41cc71b3112319a279917f6fd68d8b162f))
* adding bridge component, request-handler interface and transport message handlers ([0483b41](https://github.com/agntcy/app-sdk/commit/0483b41f66f5409a8b2650c651224c65cc1110d8))
* Adding common package and updating factory method signatures ([dc5055c](https://github.com/agntcy/app-sdk/commit/dc5055cf9294e13e9124a99eb6bfc5658cb1d7ed))
* adding e2e distributed tracing for A2A and nats ([204618e](https://github.com/agntcy/app-sdk/commit/204618e8adc23fe41a9e28c84d88f803e722df4f))
* adding initial gateway factory with a2a client ([af72049](https://github.com/agntcy/app-sdk/commit/af72049a2a7c6c83f9ec8f2b59a8b2a30ca9c54e))
* adding nats retry logic and error handling ([ffda750](https://github.com/agntcy/app-sdk/commit/ffda7507ed0f92cf5312d025400c5901c5e7ec71))
* adding official a2a example server to tests ([396c422](https://github.com/agntcy/app-sdk/commit/396c42263f9946802aa2d6dfafc4fe32209bb1ee))
* adding official a2a python sdk stubs ([bdb990d](https://github.com/agntcy/app-sdk/commit/bdb990ddd8f965001425b8debe5d9dba69739e19))
* adding protocol and transport enums ([9f15807](https://github.com/agntcy/app-sdk/commit/9f15807516dc9342670feb8cfebdd89a4da98c6f))
* adding stub for getting A2A card details via topic not url ([d711798](https://github.com/agntcy/app-sdk/commit/d7117982eea7c12245e168996ab875764ae35fb7))
* adding topic generation from A2A card method ([d711798](https://github.com/agntcy/app-sdk/commit/d7117982eea7c12245e168996ab875764ae35fb7))
* Allow users to use "global slim" to connect with SLIM data-plane ([#79](https://github.com/agntcy/app-sdk/issues/79)) ([c0387ab](https://github.com/agntcy/app-sdk/commit/c0387ab111c624b8057ee69041a976f1b49a8b95))
* bump a2a-sdk version to 0.3.2 to support protobuff&gt;=0.6.0 ([#100](https://github.com/agntcy/app-sdk/issues/100)) ([7ba4480](https://github.com/agntcy/app-sdk/commit/7ba44801cc424e96b5697518ca3c1d1791d33837))
* bump ioa-observe-sdk package version to support slim backwards compatibility. ([#66](https://github.com/agntcy/app-sdk/issues/66)) ([bd53f10](https://github.com/agntcy/app-sdk/commit/bd53f10a8d36e236bab1964d30bb2ed4f215d965))
* FastMCP Integration  ([#30](https://github.com/agntcy/app-sdk/issues/30)) ([2c0c7c9](https://github.com/agntcy/app-sdk/commit/2c0c7c9c5041482c83f857821d4a01f6ddf31a7d))
* improvement in group communication ([#48](https://github.com/agntcy/app-sdk/issues/48)) ([e1865a0](https://github.com/agntcy/app-sdk/commit/e1865a0a5ab5743c377b30c95f6c9ec7ea3cb021))
* mapping a2a to nats methods ([ef88344](https://github.com/agntcy/app-sdk/commit/ef883448f4d87e99e33869b19d06eab456f6c64c))
* mcp protocol and streamablehttp transport ([#12](https://github.com/agntcy/app-sdk/issues/12)) ([60ca96f](https://github.com/agntcy/app-sdk/commit/60ca96f8bb182adf77e2bd0119dc0bdff10db85c))
* moving nats server and observability stack services to new folder and adding cli for services ([ffda750](https://github.com/agntcy/app-sdk/commit/ffda7507ed0f92cf5312d025400c5901c5e7ec71))
* overriding A2A _send_request with Nats transport. A2A over transport test passing. ([f42ee4c](https://github.com/agntcy/app-sdk/commit/f42ee4cdeca03d72ddbd058d5a0a35b138216c78))
* re-introducing support for python3.12 ([#44](https://github.com/agntcy/app-sdk/issues/44)) ([d75317d](https://github.com/agntcy/app-sdk/commit/d75317d910bcc6b7e722a2f3f92cc823a90ff651))
* removing respond_to from BaseTransport interface and replacing with publish to ([40c7363](https://github.com/agntcy/app-sdk/commit/40c736396c78718796c2e585840d847c828b5d5f))
* removing session listener from setup() to avoid multiple listening locations for clients ([#51](https://github.com/agntcy/app-sdk/issues/51)) ([024cb1a](https://github.com/agntcy/app-sdk/commit/024cb1acc4d0c26c150aed13ee9adc379e8beeb9))
* TBAC Integration for A2A (SLIM and NATS) ([#92](https://github.com/agntcy/app-sdk/issues/92))  ([a2a7e3b](https://github.com/agntcy/app-sdk/commit/a2a7e3bb3dce00aeb5ca428de52f814173d51283))
* TBAC Integration for MCP ([#80](https://github.com/agntcy/app-sdk/issues/80)) ([3f1cb95](https://github.com/agntcy/app-sdk/commit/3f1cb954901314e0839885b3cd8e9c1c41823a4d))
* update ioa-observe-sdk to 1.0.22 and add NATS auto-instrumentation ([#63](https://github.com/agntcy/app-sdk/issues/63)) ([e8c4b59](https://github.com/agntcy/app-sdk/commit/e8c4b59550e7c615fa58bd43d0e43cd7164d8948))
* update mcp and a2a usage guide and add examples folder ([5139a44](https://github.com/agntcy/app-sdk/commit/5139a44e2072e5f3ce52d4f89760b96a5dbe3bb3))
* Update mcp and a2a usage guides to use latest factory api and a… ([#105](https://github.com/agntcy/app-sdk/issues/105)) ([5b47d79](https://github.com/agntcy/app-sdk/commit/5b47d79e16e60d827d090b62f74d1d971fc1375b))


### Bug Fixes

* add timeout to prevent delete_session hangs ([#54](https://github.com/agntcy/app-sdk/issues/54)) ([7c170ff](https://github.com/agntcy/app-sdk/commit/7c170fff5c278629a9b8d0271a91234617201a6e))
* Address FastMCP Client Improperly Disconnects Upstream A2A Server ([#93](https://github.com/agntcy/app-sdk/issues/93)) ([ba6af25](https://github.com/agntcy/app-sdk/commit/ba6af2595ed450ee62c175ee9dbe1e3422d5b236))
* bump observe sdk ([#28](https://github.com/agntcy/app-sdk/issues/28)) ([b4896b3](https://github.com/agntcy/app-sdk/commit/b4896b36826039b60a4b6a4a8abc88ece02931c7))
* **ci:** use correct secret name for release-please token ([#116](https://github.com/agntcy/app-sdk/issues/116)) ([3c7f120](https://github.com/agntcy/app-sdk/commit/3c7f120986206adf85fcd3fe01a75566bb61044b))
* fix link to docker-compose.yaml on a deleted remote branch ([#18](https://github.com/agntcy/app-sdk/issues/18)) ([6bc5b81](https://github.com/agntcy/app-sdk/commit/6bc5b810f4e5b44381dc2c89d180c58ab4de0d26))
* fixing usage-guide example sdk install command ([#17](https://github.com/agntcy/app-sdk/issues/17)) ([b8d3ab8](https://github.com/agntcy/app-sdk/commit/b8d3ab8fe588be7f1875fff6ffd89e2b79c3a7cb))
* Remove maxos-13 runner from GHA as it has been deprecated ([#82](https://github.com/agntcy/app-sdk/issues/82)) ([405a580](https://github.com/agntcy/app-sdk/commit/405a5806d345de04270b46026af21012221b70b4))
* tag must match x.y.x regex to pass ci release job ([#75](https://github.com/agntcy/app-sdk/issues/75)) ([1b31b93](https://github.com/agntcy/app-sdk/commit/1b31b931f5bc686a3db348d21d9ba9ef8aa02d67))


### Documentation

* adding jaeger trace image to README ([ffda750](https://github.com/agntcy/app-sdk/commit/ffda7507ed0f92cf5312d025400c5901c5e7ec71))
* adding mkdocs site ([0238d92](https://github.com/agntcy/app-sdk/commit/0238d92eee83a15865243b33ef256702080f4ccd))
* update readme ([ec09dd3](https://github.com/agntcy/app-sdk/commit/ec09dd3054c9de95de167fe09d50f32268673986))
* Update readme test section ([189314d](https://github.com/agntcy/app-sdk/commit/189314d75ae053360207a9a0f4b56bb5b0984cb2))

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
