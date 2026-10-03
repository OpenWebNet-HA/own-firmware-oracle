# Security policy

## Reporting a vulnerability

Please **don't** open a public issue. Report it privately instead, through the
repository's **Security → Report a vulnerability** form (GitHub private vulnerability
reporting). We'll acknowledge it and keep you updated until a fix is released.

This applies in particular to:

- anything that gets a vendor binary, disassembly or decompiled code past
  `tools/guard.py` into the repository;
- a crafted catalog entry or firmware archive that makes `fwfetch.py` / `unpack.py`
  write outside their work directory, fetch from a host outside the allow list, run a
  command, or exhaust the runner;
- a workflow path that exposes the `firmware` environment's secrets, or a write
  token, to a fork pull request.

## Scope

This covers the tooling, workflows and published results in this repository. Bugs
in the firmware itself are out of scope; report those to the vendor.
