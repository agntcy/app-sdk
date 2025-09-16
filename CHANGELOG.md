# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.3

### Added
- SLIM multi-session lifecycle management
- SLIM groupchat sessions, initiated with A2AClient.broadcast_message(group_chat=True)

### Changed
- AgntcyFactory.create_transport requires a name field when the type is SLIM, in the form /org/namespace/service
- A2AClient.broadcast_message, when created from factory, requires list of recipients to fulfill SLIM requirements

### Fixed
