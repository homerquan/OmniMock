# Architecture foundation

OmniMock follows a ports-and-adapters layout. Domain values and the behavior
engine are framework-independent; configuration and persistence are
infrastructure adapters; the stdlib HTTP adapter projects one runtime to HTTP,
SSE/WebSocket, filesystem, and MCP-shaped endpoints.

The request path is:

```text
normalize -> validate -> match rule -> plan mutations -> validate output
-> atomic commit -> journal/events -> protocol projection
```

Rules, state, and materialized artifacts precede live model access. The
foundation ships with a disabled model profile so tests and local serving never
need network access.
