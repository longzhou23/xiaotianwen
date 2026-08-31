# P1 isolated integration tests

This directory is reserved for a disposable AstrBot + Fake OneBot + Fake
Provider integration environment.  It is deliberately empty in P0.

The P0 `integration` profile reports `NOT VERIFIED` and never probes existing
ports such as 6200, 8001, 5099, or 6081.  When P1 lands, all mounted data,
ports, logs and containers must be created under the selected run sandbox.
