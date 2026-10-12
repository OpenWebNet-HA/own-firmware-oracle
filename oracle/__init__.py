"""oracle -- phase 2: run firmware translators against a simulated SCS bus.

See docs/oracle-architecture.md. Every module in this package except the
future qemu adapters works without firmware and is unit-tested in pr.yml.
"""

# Bumped whenever a change here can alter a recorded TSV; it is written to
# every record header and is part of the staleness key.
# 2: the harness records the gateway reply (ack/nack) on targets whose results
#    were made before that changed (found by suites.yml: F453AV energy-params
#    and others re-ran with the same inputs but a different reply column).
# 3: the reply wait outlasts a slow cold start (a late ACK/NACK was recorded as
#    "-") and the settle window grew from 50/300 ms to 1000/3000 ms: on slow
#    runners a bus frame, or the death of a crashed program, landed on the
#    NEXT step (or was noticed a step late), so two runs shifted rows against
#    each other. Found by suites.yml: F450, F453AV, F459, F460, F461, H4684 and
#    MH202 suites disagreed between two runs, or with the committed file, on
#    exactly those columns. settle() also watches the programs for 2 s after
#    the bus is quiet, so a crash shortly after the answer is recorded on its
#    own step in every run.
ORACLE_VERSION = "3"
