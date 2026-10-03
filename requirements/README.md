# CI requirements

`*.in` lists what each CI job needs. `*.txt` is the lock file pip-compile makes from
it: every package, including transitive dependencies, pinned with its SHA-256 hashes.
CI installs with `pip install --require-hashes -r requirements/<job>.txt`. A package
that is missing from the lock file, or whose bytes don't match a hash, fails the
install.

| File | Used by |
|---|---|
| `lint` | `pr.yml` lint job: ruff, mypy, zizmor |
| `test` | `pr.yml` test job (Python 3.12 to 3.14): pytest, coverage |
| `oracle` | `oracle.yml`, `reproduce.yml`: pyyaml, boto3 (R2 sources only) |

Dependabot re-locks these files every month. To change one by hand, edit the `.in`
file and re-lock it:

```bash
cd requirements
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --newline LF lint.in
```

Run pip-compile on Python 3.12, the oldest Python CI supports.
