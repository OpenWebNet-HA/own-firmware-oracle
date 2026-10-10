"""oracle -- phase 2: run firmware translators against a simulated SCS bus.

See docs/oracle-architecture.md. Every module in this package except the
future qemu adapters works without firmware and is unit-tested in pr.yml.
"""

# Bumped whenever a change here can alter a recorded TSV; it is written to
# every record header and is part of the staleness key.
# 2: the harness records the gateway reply (ack/nack) on targets whose results
#    were made before that changed (found by suites.yml: F453AV energy-params
#    and others re-ran with the same inputs but a different reply column).
ORACLE_VERSION = "2"
