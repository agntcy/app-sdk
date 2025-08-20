# SLIM2Transport: A2A over SLIM v0.4.0

A production-ready transport implementation that enables A2A (Agent-to-Agent) protocol communication over SLIM v0.4.0 messaging infrastructure. This transport provides seamless integration between the A2A protocol layer and SLIM's high-performance messaging capabilities.

## Architecture Overview

### Full Stack Integration

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           A2A Application Layer                          │
├─────────────────────────────────────────────────────────────────────────┤
│  Corto Agent    │  Generic Agent  │  Farm Agent    │  Exchange Agent    │
├─────────────────┼─────────────────┼────────────────┼────────────────────┤
│                         A2A Protocol Layer                              │
│  ┌─────────────────┐              ┌─────────────────┐                   │
│  │   A2AProtocol   │◄────────────►│  MessageBridge  │                   │
│  └─────────────────┘              └─────────────────┘                   │
├─────────────────────────────────────────────────────────────────────────┤
│                        Transport Layer                                  │
│                    ┌─────────────────┐                                  │
│                    │  SLIM2Transport │                                  │
│                    └─────────────────┘                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                      SLIM v0.4.0 Layer                                 │
│  ┌─────────────────┐              ┌─────────────────┐                   │
│  │  SLIM Client    │◄────────────►│  SLIM Server    │                   │
│  │  (Publisher)    │   TCP/HTTP    │  (Receiver)     │                   │
│  └─────────────────┘              └─────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Message Flow Diagram

```
Agent A                   SLIM2Transport           SLIM Server           SLIM2Transport                   Agent B
   │                           │                        │                        │                           │
   │ 1. Send A2A Message       │                        │                        │                           │
   ├──────────────────────────►│                        │                        │                           │
   │                           │ 2. Create SLIM Session │                        │                           │
   │                           ├───────────────────────►│                        │                           │
   │                           │                        │ 3. Route to Agent B    │                           │
   │                           │                        ├───────────────────────►│                           │
   │                           │                        │                        │ 4. Deliver Message        │
   │                           │                        │                        ├──────────────────────────►│
   │                           │                        │                        │                           │
   │                           │                        │                        │ 5. Generate Response      │
   │                           │                        │                        │◄──────────────────────────┤
   │                           │                        │ 6. Send Reply          │                           │
   │                           │                        │◄───────────────────────┤                           │
   │                           │ 7. Receive Reply       │                        │                           │
   │                           │◄───────────────────────┤                        │                           │
   │ 8. Return Response        │                        │                        │                           │
   │◄──────────────────────────┤                        │                        │                           │
```

## Key Features

- **🔄 Seamless A2A Integration** - Full compatibility with A2A protocol and MessageBridge
- **🏭 Factory Pattern Support** - Easy instantiation via AgntcyFactory
- **🔒 Robust Session Management** - Proper lifecycle management with concurrent request handling
- **⚡ High Performance** - Optimized for low-latency, high-throughput messaging
- **🛡️ Production-Ready** - Comprehensive error handling, retries, and monitoring
- **📊 Observability** - Detailed logging and metrics for debugging and monitoring

## Usage

### Basic Usage

```python
from agntcy_app_sdk.transports.slim2 import SLIM2Transport
from agntcy_app_sdk.protocols.a2a.message import Message

# Initialize transport
transport = SLIM2Transport(
    endpoint="http://localhost:46357",
    org="test",
    namespace="demo", 
    identity="agent1",
    shared_secret="secret"
)

# Send message with response expected
message = Message(type="request", payload="Hello World")
response = await transport.publish(
    topic="receiver", 
    message=message, 
    respond=True
)
```

### Factory Pattern (Recommended)

```python
from agntcy_app_sdk.factory import AgntcyFactory, TransportTypes

# Create transport via factory
transport = AgntcyFactory.create_transport(
    TransportTypes.SLIM2,
    endpoint="http://localhost:46357",
    org="production",
    namespace="agents",
    identity="corto-farm-1",
    shared_secret=os.getenv("SLIM_SECRET")
)

# Create bridge with your agent
bridge = AgntcyFactory.create_bridge(transport, your_agent)
await bridge.start()
```

### Integration with Corto Agents

```python
from agntcy_app_sdk.factory import AgntcyFactory, TransportTypes
from agntcy_app_sdk.agents.corto import CortoFarmAgent

# Setup Corto farm agent with SLIM2Transport
transport = AgntcyFactory.create_transport(
    TransportTypes.SLIM2,
    endpoint=os.getenv("SLIM_ENDPOINT", "http://localhost:46357"),
    org="production",
    namespace="corto",
    identity="farm-agent-1",
    shared_secret=os.getenv("SLIM_SECRET")
)

agent = CortoFarmAgent(
    agent_id="farm-agent-1",
    crops=["corn", "wheat", "soybeans"]
)

bridge = AgntcyFactory.create_bridge(transport, agent)
await bridge.start()
```

## Configuration

| Parameter | Default | Description | Environment Variable |
|-----------|---------|-------------|---------------------|
| `endpoint` | `http://localhost:46357` | SLIM server endpoint | `SLIM_ENDPOINT` |
| `org` | `default` | Organization namespace | `SLIM_ORG` |
| `namespace` | `default` | Service namespace | `SLIM_NAMESPACE` |
| `identity` | `agent` | Agent identity name | `SLIM_IDENTITY` |
| `shared_secret` | `secret` | Authentication secret | `SLIM_SECRET` |
| `connection_timeout` | `10.0` | Connection timeout (seconds) | `SLIM_CONNECTION_TIMEOUT` |
| `request_timeout` | `30.0` | Request timeout (seconds) | `SLIM_REQUEST_TIMEOUT` |
| `max_retries` | `3` | Connection retry limit | `SLIM_MAX_RETRIES` |

### Environment Configuration

```bash
export SLIM_ENDPOINT="http://your-slim-server:46357"
export SLIM_ORG="your-organization"
export SLIM_NAMESPACE="your-namespace"
export SLIM_IDENTITY="your-agent-id"
export SLIM_SECRET="your-shared-secret"
```

## API Reference

### Core Methods

#### `publish(topic: str, message: Message, respond: bool = False)`
Publishes a message to the specified topic.

**Parameters:**
- `topic`: Destination agent/topic identifier
- `message`: A2A protocol message object
- `respond`: Whether to wait for and return a response

**Returns:** Response message if `respond=True`, otherwise `None`

#### `subscribe(topic: str, handler: Callable)`
Subscribes to incoming messages on a topic.

**Parameters:**
- `topic`: Topic to subscribe to (typically agent's own identity)
- `handler`: Async function to handle incoming messages

#### `start()` / `stop()`
Lifecycle management methods for the transport.

## Error Handling

The transport provides comprehensive error handling:

### Connection Management
- **Connection failures** - Automatic retry with exponential backoff
- **Network timeouts** - Configurable timeouts with proper cleanup
- **Server unavailable** - Graceful degradation and recovery

### Session Management
- **Session creation errors** - Proper error propagation and cleanup
- **Session timeouts** - Automatic session cleanup and retry
- **Concurrent session limits** - Built-in session pooling and queuing

### Message Processing
- **Serialization errors** - JSON encoding/decoding error handling
- **Message validation** - A2A protocol compliance checking
- **Delivery failures** - Retry logic with configurable limits

### Example Error Handling

```python
try:
    response = await transport.publish("receiver", message, respond=True)
except ConnectionError as e:
    logger.error(f"Failed to connect to SLIM server: {e}")
    # Implement fallback or retry logic
except TimeoutError as e:
    logger.warning(f"Request timed out: {e}")
    # Handle timeout scenario
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    # Generic error handling
```

## Performance Considerations

### Connection Pooling
SLIM2Transport creates new SLIM client instances per request for isolation and thread safety. While this provides robustness, consider:

- **High-throughput scenarios**: Monitor connection overhead
- **Resource constraints**: Adjust `max_retries` and timeout values
- **Concurrent requests**: Transport handles concurrency automatically

### Monitoring and Metrics

```python
import logging

# Enable detailed logging
logging.getLogger('agntcy_app_sdk.transports.slim2').setLevel(logging.DEBUG)

# Key metrics to monitor:
# - Connection establishment time
# - Message round-trip time  
# - Session creation/cleanup rate
# - Error rates by type
```

## Migration from Legacy Transports

### From SLIM v0.3.x

1. Update SLIM bindings to v0.4.0+
2. Replace transport initialization:
   ```python
   # Old
   from agntcy_app_sdk.transports.slim import SLIMTransport
   
   # New  
   from agntcy_app_sdk.transports.slim2 import SLIM2Transport
   ```
3. Update factory usage:
   ```python
   # Old
   transport = AgntcyFactory.create_transport(TransportTypes.SLIM, ...)
   
   # New
   transport = AgntcyFactory.create_transport(TransportTypes.SLIM2, ...)
   ```

### API Changes
- `receive_back()` method is now a no-op (simplified message handling)
- Session management is fully automated
- Error handling is more granular and specific

## Examples

See the `examples/` directory for complete usage examples:
- `simple_client.py` - Basic request-reply messaging
- `simple_server.py` - Message handling and response
- `factory_usage.py` - Using AgntcyFactory with various agent types

## Troubleshooting

### Common Issues

**"Connection refused"**
- Verify SLIM server is running on specified endpoint
- Check firewall and network connectivity
- Ensure correct endpoint format (`http://host:port`)

**"Authentication failed"**
- Verify `shared_secret` matches server configuration
- Check `org` and `namespace` values
- Ensure identity is unique within namespace

**"Session timeout"**
- Increase `request_timeout` for long-running operations
- Monitor server load and response times
- Check for network latency issues

**"Message delivery failed"**
- Verify target topic/agent exists and is subscribed
- Check message format and A2A protocol compliance
- Monitor server logs for routing issues

### Debug Mode

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Enables detailed transport logging
transport = SLIM2Transport(..., debug=True)
```
