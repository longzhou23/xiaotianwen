# P2 performance tests

Performance measurements are not a P0 correctness signal.  The repeatable
summary, 10% P95 regression check, 100-slot non-sending Canary plan and 24/72
hour observation templates live in
`plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/p2/performance.py`.
They accept only synthetic metric samples and never call a real Provider or
send QQ messages.  A sample count below the declared minimum is
`INSUFFICIENT_DATA`, not a pass.
