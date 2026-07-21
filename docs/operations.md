# Operations

Use `omnimock validate` before starting services. `omnimock doctor` reports the
loopback, outbound-network, generation-mode, and state-driver posture.

Runtime state and snapshots are written below `.omnimock/`, which is ignored by
version control. `omnimock state reset` restores the scenario baseline, while
`omnimock snapshot create NAME` and `omnimock snapshot restore NAME` provide
repeatable local checkpoints.

The default server binds only to loopback. Do not enable proxy or outbound
webhook behavior without an explicit allowlist and security review.
