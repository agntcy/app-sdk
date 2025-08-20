# SLIM2Transport: A2A over SLIM v0.4.0

## A2A Request-Reply Communication Flow

```
  ┌─────────────┐        ┌──────────────────┐        ┌─────────────┐        ┌──────────────────┐        ┌─────────────┐
  │ A2A Client  │        │  SLIM2Transport  │        │ SLIM Server │        │  SLIM2Transport  │        │ A2A Server  │
  │             │        │   (Publisher)    │        │             │        │  (Subscriber)    │        │             │
  └─────────────┘        └──────────────────┘        └─────────────┘        └──────────────────┘        └─────────────┘
        │                         │                         │                         │                         │
        │                         │                         │      ╔══════════════════════════════════════╗      │
        │                         │                         │      ║           Server Setup               ║      │
        │                         │                         │      ║  • Slim.new(receiver_name)         ║      │
        │                         │                         │      ║  • slim.connect(endpoint)           ║      │
        │                         │                         │      ║  • slim.subscribe(topic)            ║      │
        │                         │                         │      ║  • slim.receive() -> await session  ║      │
        │                         │                         │      ╚══════════════════════════════════════╝      │
        │                         │                         │                         │                         │
        │ 1. publish(topic, msg)  │                         │                         │                         │
        ├────────────────────────►│                         │                         │                         │
        │                         │ 2. Initialize SLIM      │                         │                         │
        │                         │  • Slim.new(sender)     │                         │                         │
        │                         │  • connect(endpoint)    │                         │                         │
        │                         │  • subscribe(sender)    │                         │                         │
        │                         │                         │                         │                         │
        │                         │ 3. Setup Session        │                         │                         │
        │                         │  • create_session()     │                         │                         │
        │                         │  • set_route(receiver)  │                         │                         │
        │                         │                         │                         │                         │
        │                         │ 4. Send Request         │                         │                         │
        │                         │  ┌─────────────────────┐ │  ═══════════════════════▶  │ 5. Receive Message     │
        │                         │  │ request_reply(      │ │     SLIM Protocol     │  │  • receive(session)    │
        │                         │  │   session,          │ │                       │  │  • deserialize A2A     │
        │                         │  │   message,          │ │                       │  │                        │
        │                         │  │   receiver,         │ │                       │  │ 6. Process via A2A     │
        │                         │  │   timeout=30s       │ │                       │  ├───────────────────────►│ handler(msg)
        │                         │  │ )                   │ │                       │  │                        │ ┌─────────────┐
        │                         │  └─────────────────────┘ │                       │  │                        │ │  Process    │
        │                         │                         │                       │  │                        │ │  Business   │
        │                         │  ┌─────────────────────┐ │                       │  │ 7. Send Response       │ │  Logic      │
        │                         │  │ await response      │ │  ◄═══════════════════════  │  • publish_to(session, │ │             │
        │                         │  │                     │ │     SLIM Protocol     │  │    response)           │ └─────────────┘
        │                         │  └─────────────────────┘ │                       │  │                        │        │
        │                         │                         │                       │  │                        │◄───────┘
        │◄────────────────────────┤ 8. Return A2A Response  │                       │  │                        │ return
        │                         │                         │                       │  │                        │ response
        │                         │                         │                       │  │                        │
```

## Implementation Details

### Client Side: `publish(topic, message, respond=True)`
```python
# 1. Initialize SLIM client with authentication
sender_name = PyName(org, namespace, identity)
receiver_name = PyName(org, namespace, sanitized_topic)
slim = await Slim.new(sender_name, provider, verifier)

# 2. Connect and setup routing
aws slim.connect({"endpoint": endpoint, "tls": {"insecure": True}})
await slim.subscribe(sender_name)  # For receiving replies
session = await slim.create_session(PySessionConfiguration.FireAndForget())
await slim.set_route(receiver_name)

# 3. Send request and wait for response
response = await slim.request_reply(session, message, receiver_name, timeout=30.0)
return Message.deserialize(response)
```

### Server Side: `subscribe(topic, handler)`  
```python
# 1. Initialize SLIM subscriber with authentication
receiver_name = PyName(org, namespace, sanitized_topic)
slim = await Slim.new(receiver_name, provider, verifier)

# 2. Connect and subscribe to topic
await slim.connect({"endpoint": endpoint, "tls": {"insecure": True}})
await slim.subscribe(receiver_name)

# 3. Message handling loop
while True:
    session_info = await slim.receive()  # Wait for new session
    message_bytes = await slim.receive(session=session_info.session_id)
    
    # Process message via A2A callback
    message = Message.deserialize(message_bytes)
    response = await handler(message)
    
    # Send response back through same session
    await slim.publish_to(session_info, response.serialize())
```

### Authentication & Identity
```python
# Shared secret authentication (default for testing)
provider = PyIdentityProvider.SharedSecret(shared_secret)
verifier = PyIdentityVerifier.SharedSecret(shared_secret)

# Agent identity: org/namespace/identity
# Communication topic: org/namespace/sanitized_agent_name
```
