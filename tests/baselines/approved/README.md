# Approved baseline storage

This directory is intentionally empty in the initial P0 delivery.

An approved Golden is a review decision, not a by-product of a test run.  The
normal `run` and `compare` commands only read this directory.  A reviewer must
explicitly inspect the structural diff and then use:

```text
python -m tests.harness.cli approve-baseline --case <case-id> --reason "<why>"
```

The command prompts for a confirmation on an interactive terminal (or requires
`--yes` in a noninteractive workflow).  It writes only the selected case and
records the approval reason; it never rewrites every baseline automatically.
