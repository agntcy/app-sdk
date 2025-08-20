# SLIM3 Transport

Simple SLIM v0.4.0 transport for coffee farm communication.

## Architecture Diagram

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   MODERATOR     │    │   SLIM SERVER    │    │  PARTICIPANTS   │
│   (Exchange)    │    │  :46357          │    │  (Farms)        │
├─────────────────┤    ├──────────────────┤    ├─────────────────┤
│ • Creates       │◄──►│ • Session Mgmt   │◄──►│ • Wait for      │
│   sessions      │    │ • Message Route  │    │   invitations   │
│ • Invites farms │    │ • Authentication │    │ • Send to group │
│ • Broadcasts    │    │ • MLS Encryption │    │   channel only  │
│ • Direct msgs   │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────────────┐
                    │      CHANNELS           │
                    ├─────────────────────────┤
                    │ • group (shared)        │
                    │ • vietnam (direct)      │
                    │ • colombia (direct)     │
                    │ • brazil (direct)       │
                    └─────────────────────────┘
```

## A2A over SLIM3 Communication Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           A2A COMMUNICATION OVER SLIM3                         │
└─────────────────────────────────────────────────────────────────────────────────┘

1. AUTHENTICATION & SESSION SETUP
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   MODERATOR     │    │   SLIM SERVER    │    │  PARTICIPANT    │
│                 │    │                  │    │                 │
│ ┌─────────────┐ │    │ ┌──────────────┐ │    │ ┌─────────────┐ │
│ │PyIdentity   │─┼────┼►│Verify Auth   │◄┼────┼─│PyIdentity   │ │
│ │Provider     │ │    │ │SharedSecret  │ │    │ │Verifier     │ │
│ └─────────────┘ │    │ └──────────────┘ │    │ └─────────────┘ │
└─────────────────┘    └──────────────────┘    └─────────────────┘

2. SESSION CREATION (Moderator Only)
┌─────────────────┐                            ┌─────────────────┐
│   MODERATOR     │    create_streaming_session │  PARTICIPANT    │
│                 │──────────────────────────────┼►│  (Invited)     │
│ ┌─────────────┐ │    invite("vietnam")        │ │               │
│ │Session Mgmt │ │──────────────────────────────┼►│  Wait for     │
│ └─────────────┘ │                            │ │  invitation   │
└─────────────────┘                            └─────────────────┘

3. MESSAGE PATTERNS

A) FIRE-AND-FORGET
┌─────────────────┐                            ┌─────────────────┐
│   SENDER        │    publish(topic, msg)     │   RECEIVER      │
│                 │──────────────────────────►│                 │
│                 │                            │                 │
└─────────────────┘                            └─────────────────┘

B) REQUEST-REPLY  
┌─────────────────┐                            ┌─────────────────┐
│   SENDER        │    publish(topic, msg,     │   RECEIVER      │
│                 │            respond=True)   │                 │
│                 │──────────────────────────►│                 │
│                 │◄──────────────────────────│  return msg     │
│   Process reply │                            │                 │
└─────────────────┘                            └─────────────────┘

C) BROADCAST (Moderator Only)
┌─────────────────┐    broadcast(topic, msg,   ┌─────────────────┐
│   MODERATOR     │    expected=3, timeout=30) │  PARTICIPANT 1  │
│                 │──────────────────────────►│                 │
│                 │                            │  Auto-respond   │
│                 │◄──────────────────────────│                 │
│                 │                            └─────────────────┘
│                 │                            ┌─────────────────┐
│                 │──────────────────────────►│  PARTICIPANT 2  │
│                 │                            │                 │
│                 │◄──────────────────────────│  Auto-respond   │
│                 │                            └─────────────────┘
│                 │                            ┌─────────────────┐
│                 │──────────────────────────►│  PARTICIPANT 3  │
│                 │                            │                 │
│                 │◄──────────────────────────│  Auto-respond   │
│  Collect all    │                            └─────────────────┘
│  responses      │
└─────────────────┘

4. CHANNEL ROUTING RULES

MODERATOR RULES:
┌─────────────────┐    Can send to any channel
│   MODERATOR     │    ┌─────────────────────┐
│                 │───►│ group (broadcast)   │
│                 │───►│ vietnam (direct)    │
│                 │───►│ colombia (direct)   │
│                 │───►│ brazil (direct)     │
└─────────────────┘    └─────────────────────┘

PARTICIPANT RULES:
┌─────────────────┐    Always routes to group
│  PARTICIPANT    │    ┌─────────────────────┐
│                 │───►│ group (only)        │
│  publish("any") │    │                     │
│     ↓           │    │ Ignores topic param │
│   "group"       │    │                     │
└─────────────────┘    └─────────────────────┘

5. ERROR HANDLING & TIMEOUTS

┌─────────────────┐    Connection Failed      ┌─────────────────┐
│   AGENT         │◄──────────────────────────│  SLIM SERVER    │
│                 │    Retry (exponential     │                 │
│                 │    backoff)               │                 │
│                 │──────────────────────────►│                 │
│   Timeout       │    Success                │                 │
│   Management    │◄──────────────────────────│                 │
└─────────────────┘                           └─────────────────┘
```

## Environment Setup

```bash
# Required
export SLIM_SHARED_SECRET="coffee-secret-2024"
export SLIM_IDENTITY="moderator-exchange"  # or "vietnam-farm"

# Optional
export SLIM_ENDPOINT="http://localhost:46357"
export MODERATOR_NAME="moderator"
export GROUP_CHANNEL="group"
export PARTICIPANT_CHANNELS="vietnam,colombia,brazil"
```

## Usage

### Create Transport
```python
from agntcy_app_sdk.transports.slim3 import SLIM3Transport

transport = SLIM3Transport()
```

### Methods

#### `publish(topic, message, respond=False)`
```python
# Moderator: Direct to farm
await transport.publish("vietnam", Message(payload="Status?"))

# Moderator: To group  
await transport.publish("group", Message(payload="Hello all"))

# Participant: Always to group
await transport.publish("anything", Message(payload="Reply"))
```

#### `broadcast(topic, message, expected_responses, timeout)`
```python
# Moderator only
await transport.broadcast(
    "group", 
    Message(payload="Meeting now"),
    expected_responses=3,
    timeout=30.0
)
```

#### `subscribe(topic, callback)`
```python
async def handler(msg):
    return Message(payload="Received")

await transport.subscribe("vietnam", handler)
```

## Flow

1. **Moderator** creates sessions for channels
2. **Moderator** invites participants to group channel
3. **Participants** wait for invitations, then send to group
4. **Moderator** can send to group or direct to specific farms

## Role Detection

- Contains `MODERATOR_NAME` in identity = Moderator
- Otherwise = Participant

## A2A over SLIM3: Coffee Exchange Example

Real-world example from Lungo coffee trading system showing A2A protocol over SLIM3 transport.

### Architecture: Coffee Exchange + Farms
```
┌─────────────────────────┐    SLIM3    ┌─────────────────────────┐
│    COFFEE EXCHANGE      │◄───────────►│      COFFEE FARMS       │
│    (Supervisor)         │   Transport  │      (Workers)          │
├─────────────────────────┤             ├─────────────────────────┤
│ • FastAPI Server        │             │ • Brazil Farm Server    │
│ • LangGraph Supervisor  │             │ • Colombia Farm Server  │
│ • A2A Client           │             │ • Vietnam Farm Server   │
│ • Inventory Management  │             │ • A2A Servers           │
│ • Order Processing      │             │ • LangGraph Workers     │
└─────────────────────────┘             └─────────────────────────┘
```

### Environment Setup
```bash
# Exchange (Moderator)
export SLIM_IDENTITY="coffee-exchange"
export SLIM_SHARED_SECRET="coffee-secret-2024"

# Farm Agents (Participants)  
export SLIM_IDENTITY="vietnam-farm"    # or "colombia-farm", "brazil-farm"
export SLIM_SHARED_SECRET="coffee-secret-2024"
```

### Code Example: Exchange Client
```python
from agntcy_app_sdk.factory import AgntcyFactory

# Create factory and transport
factory = AgntcyFactory("coffee.exchange", enable_tracing=True)
transport = factory.create_transport("SLIM3")

# Create A2A clients for each farm
vietnam_client = await factory.create_client(
    protocol="A2A",
    agent_topic="vietnam-farm_v1.0",
    transport=transport
)

colombia_client = await factory.create_client(
    protocol="A2A", 
    agent_topic="colombia-farm_v1.0",
    transport=transport
)
```

### Real User Interactions

#### 1. Inventory Check (Broadcast)
```python
# User prompt: "Show me total inventory across all farms"
from a2a.types import SendMessageRequest

# Exchange broadcasts to all farms
request = SendMessageRequest(
    method="get_inventory",
    params={"request_type": "total"}
)

responses = await client.broadcast_message(
    request,
    expected_responses=3,  # brazil, colombia, vietnam
    timeout=30.0
)

# Each farm responds:
# Brazil: {"coffee_beans": 150, "price_per_lb": 0.45}
# Colombia: {"coffee_beans": 200, "price_per_lb": 0.55} 
# Vietnam: {"coffee_beans": 180, "price_per_lb": 0.40}
```

#### 2. Specific Farm Query (Direct)
```python
# User prompt: "How much coffee does Colombia farm have?"

response = await colombia_client.send_message(
    method="get_inventory",
    params={"farm": "colombia"}
)

# Colombia responds: {"coffee_beans": 200, "price_per_lb": 0.55}
```

#### 3. Order Placement (Direct)
```python
# User prompt: "I need 50 lb coffee from Colombia for $0.50/lb"

order_response = await colombia_client.send_message(
    method="create_order",
    params={
        "quantity": 50,
        "price_per_lb": 0.50,
        "farm": "colombia"
    }
)

# Colombia responds: {"order_id": "COL-001", "status": "confirmed"}
```

### Message Flow
```
1. USER PROMPT
   ┌─────────────────┐
   │ "Show inventory │
   │  across farms"  │
   └─────────┬───────┘
             │
2. EXCHANGE PROCESSING
   ┌─────────▼───────┐    ┌──────────────────┐
   │ FastAPI Server  │───►│ LangGraph Node   │
   │ /agent/prompt   │    │ "Inventory"      │
   └─────────────────┘    └─────────┬────────┘
                                   │
3. A2A over SLIM3 BROADCAST
   ┌─────────▼───────┐    ┌──────────────────┐    ┌─────────────────┐
   │ A2A Client      │───►│ SLIM3 Transport  │───►│ Farm A2A Servers│
   │ JSON-RPC call   │    │ Session + Auth   │    │ Vietnam/Colombia│
   └─────────────────┘    └──────────────────┘    │ /Brazil         │
                                                  └─────────┬───────┘
4. FARM PROCESSING                                         │
   ┌─────────▼───────┐    ┌──────────────────┐             │
   │ LangGraph Node  │───►│ Inventory Data   │◄────────────┘
   │ "Get Inventory" │    │ Response         │
   └─────────────────┘    └─────────┬────────┘
                                   │
5. AGGREGATED RESPONSE                        │
   ┌─────────▼───────┐             │
   │ Total: 530 lbs  │◄────────────┘
   │ Avg: $0.47/lb   │
   └─────────────────┘
```

### Benefits for Coffee Trading
- **Broadcast Inventory**: Query all farms simultaneously
- **Direct Orders**: Send orders to specific farms  
- **Real-time Updates**: Farms push inventory changes
- **Scalable**: Add new farms without code changes
- **Reliable**: SLIM3 handles authentication and retry logic
