# Operations

Use `omnimock validate` before starting services. `omnimock doctor` reports the
loopback, outbound-network, generation-mode, and state-driver posture.

Runtime state and snapshots are written below `.omnimock/`, which is ignored by
version control. `omnimock state reset` restores the scenario baseline, while
`omnimock snapshot create NAME` and `omnimock snapshot restore NAME` provide
repeatable local checkpoints.

The default server binds only to loopback. Do not enable proxy or outbound
webhook behavior without an explicit allowlist and security review.

HTTP services may reference a project-relative `behavior` file conforming to
`schemas/http-manifest.schema.json`. OmniMock validates these manifests before
binding, expands their declared base paths, bounds request/response and
WebSocket data, and rejects absolute paths or references that resolve outside
the project root. Use `omnimock inspect routes` to review the expanded surface.

CLI services use project-relative `behavior` files conforming to
`schemas/cli-manifest.schema.json`. `omnimock mock-cli -- ARGS...` matches the
argument vector as data and writes the declared stdout, stderr, and exit code.
It never invokes a shell or a manifest-defined executable. Use
`omnimock inspect commands` to review the accepted command surface.
