# P1 isolated integration tests

The first P1 slice is now executable without Docker: the repository CLI
`run --profile integration` runs `tests/harness/p1_integration.py`, which uses
the disposable `FakeAstrBotRuntime` and writes only to the selected run
sandbox.  It covers normalized input, stream/usage, tool continuation, shadow
no-dispatch and explicit `NOT_CONNECTED` observations.

This is not evidence that a real AstrBot process or Plugin Page is compatible.
The profile reports `NOT_VERIFIED` for that remaining layer and never probes
existing ports such as 6200, 8001, 5099, or 6081.  When the real isolated
instance is added, all mounted data, ports, logs and containers must be created
under the selected run sandbox.
