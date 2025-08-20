# SLIM2Transport: A2A over SLIM v0.4.0

## Detailed A2A Client to A2A Server Communication Flow

```
┌─────────────┐    ┌──────────────────────┐    ┌─────────────┐    ┌──────────────────────┐    ┌─────────────┐
│ A2A Client  │    │ SLIM2Transport       │    │ SLIM Server │    │ SLIM2Transport       │    │ A2A Server  │
│             │    │ (Client Side)        │    │             │    │ (Server Side)        │    │             │
└─────────────┘    └──────────────────────┘    └─────────────┘    └──────────────────────┘    └─────────────┘
       │                      │                        │                      │                        │
       │                      │                        │                      │                        │
   ┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
   │ PHASE 1: INITIALIZATION                                                                               │
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
       │                      │                        │                      │                        │
       │ 1. transport.        │                        │                      │ [Server Setup]        │
       │    publish(topic,    │                        │                      │                        │
       │    message)          │                        │                      │ slim_bindings.         │
       ├─────────────────────►│                        │                      │ Slim.new()             │
       │                      │ 2. slim_bindings.      │                      │ slim.connect()         │
       │                      │    Slim.new()          │                      │ slim.subscribe()       │
       │                      │ 3. slim.connect()      │                      │ slim.receive() loop    │
       │                      │ 4. slim.subscribe()    │                      │                        │
       │                      │ 5. slim.create_session()                     │                        │
       │                      │ 6. slim.set_route()    │                      │                        │
       │                      │                        │                      │                        │
   ┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
   │ PHASE 2: MESSAGE TRANSMISSION                                                                         │
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
       │                      │                        │                      │                        │
       │                      │ 7. slim.request_reply()│                      │                        │
       │                      ├───────────────────────►│ 8. Route message     │                        │
       │                      │                        ├─────────────────────►│ 9. slim.receive()      │
       │                      │                        │                      ├───────────────────────►│ 10. handler()
       │                      │                        │                      │                        ├─────────┐
       │                      │                        │                      │                        │ Process │
       │                      │                        │                      │                        │ A2A Msg │
       │                      │                        │                      │                        │◄────────┘
   ┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
   │ PHASE 3: RESPONSE TRANSMISSION                                                                        │
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
       │                      │                        │                      │                        │
       │                      │                        │                      │◄───────────────────────┤ 11. return
       │                      │                        │                      │ 12. slim.publish_to()  │     response
       │                      │                        │ 13. Route reply      │                        │
       │                      │                        │◄─────────────────────┤                        │
       │                      │◄───────────────────────┤ 14. Return reply     │                        │
       │◄─────────────────────┤ 15. return response    │                      │                        │
       │                      │                        │                      │                        │
```

### Method Details

**SLIM2Transport Client Side (`publish` method):**
1. `slim_bindings.Slim.new(sender_name, provider, verifier)` - Create SLIM instance
2. `slim.connect({"endpoint": endpoint, "tls": {"insecure": True}})` - Connect to SLIM server
3. `slim.subscribe(sender_name)` - Subscribe to own identity for replies
4. `slim.create_session(PySessionConfiguration.FireAndForget())` - Create session
5. `slim.set_route(receiver_name)` - Set routing to target agent
6. `slim.request_reply(session, message, receiver_name, timeout)` - Send message and wait

**SLIM2Transport Server Side (`subscribe` method):**
1. `slim_bindings.Slim.new(receiver_name, provider, verifier)` - Create SLIM instance
2. `slim.connect({"endpoint": endpoint, "tls": {"insecure": True}})` - Connect to SLIM server
3. `slim.subscribe(receiver_name)` - Subscribe to own topic
4. `slim.receive()` - Wait for session info
5. `slim.receive(session=session_id)` - Get actual message
6. `handler(message)` - Process via A2A callback
7. `slim.publish_to(session, response)` - Send response back
