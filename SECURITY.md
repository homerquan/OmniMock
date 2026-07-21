# Security model

OmniMock binds to loopback by default, denies outbound network access, uses
only declarative and bounded behavior expressions, and keeps generated files
non-executable. Filesystem projections are resolved below a configured sandbox
root. Model profiles use configuration references and do not contain secrets.

Security-sensitive changes require adversarial tests for path traversal,
expression evaluation, resource limits, and secret redaction as applicable.
