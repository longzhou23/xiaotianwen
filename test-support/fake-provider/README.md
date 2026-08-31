# Fake Provider (P1)

P0 uses `tests.harness.spies.ProviderSpy` in memory.  A later isolated
container service belongs here only when it can provide deterministic streaming,
tool-call, timeout and malformed-response fixtures without credentials or
external network access.
