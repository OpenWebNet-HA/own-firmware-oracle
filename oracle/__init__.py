"""oracle -- phase 2: run firmware translators against a simulated SCS bus.

See docs/oracle-architecture.md. Every module in this package except the
future qemu adapters works without firmware and is unit-tested in pr.yml.
"""

# Bumped whenever a change here can alter a recorded TSV; it is written to
# every record header and is part of the staleness key.
ORACLE_VERSION = "1"
