# OmniMock

OmniMock is a deterministic, contract-first simulator for APIs, streams,
files, and tool services. The foundation is intentionally model-free at serve
time: rules, fixtures, and state transitions are the source of truth.

## Quick start

The project targets Python 3.12+ and has no required runtime dependencies.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
omnimock validate
omnimock generate checkout
omnimock serve checkout
```

The complete sample is in [`samples/commerce`](samples/commerce). To run it
directly, execute `omnimock --root samples/commerce validate`.

The [`samples/mirror_neuron`](samples/mirror_neuron) workspace provides
manifest-driven Mirror Neuron HTTP, bidirectional WebSocket, and `mn` CLI mocks
for developing OtterDesk without a live runtime or relay.

The sample exposes a shared scenario through:

- HTTP orders at `127.0.0.1:8081`;
- SSE events at `127.0.0.1:8082/events`;
- filesystem reads at `127.0.0.1:8083/files/...`;
- MCP-shaped JSON-RPC calls at `127.0.0.1:8084/mcp`.

Outbound network, model calls, arbitrary code execution, and host filesystem
access are denied by default. See [`SPEC.md`](SPEC.md) and
[`AGENTS.md`](AGENTS.md) for the architecture and contribution rules.

## License

OmniMock is released under the [MIT License](LICENSE).
