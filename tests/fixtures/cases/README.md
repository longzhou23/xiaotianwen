# Replay fixture catalog location

New JSON case catalogs should be created in this directory.  The harness first
looks here, then falls back to `tests/fixtures/replay/` for the existing P0
catalogs that were already being developed before this repository-level layout
was added.  The fallback is intentional compatibility, not a second execution
source: a catalog name resolves to the first matching file only.

When the initial catalog is moved in a reviewed change, update its path without
changing test IDs or silently rewriting an approved baseline.
