# Model profiles

Each JSON file is one validated, nonsecret model profile. The runtime reads
provider, model, capability, timeout, and privacy declarations from these
files; application code does not select provider or model identifiers.

`disabled.json` is the default for deterministic local and CI runs.
