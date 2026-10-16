## Summary

<!-- Briefly describe what this PR changes and why. -->

## Downstream Ecosystem Goal Alignment

- [ ] **OpenWebNet-Encyclopedia**: Updates or adds empirical `firmware_observed` evidence (`tools/export_evidence.py`)
- [ ] **openwebnet-mcp**: Updates `results/mcp_index.json` (`python tools/mcp_index.py --check` passes)
- [ ] **OWNd / MyHOME**: Validates or adds parser/gateway protocol coverage (`python tools/check.py --all --report`)
- [ ] **Infrastructure / Tooling / Documentation**: Internal oracle harness, CI, or documentation improvements

## Cryptographic Provenance & Epistemic Status

<!-- If this PR adds or modifies oracle targets, suites, or findings, record the hashes and epistemic level: -->
- **Epistemic Status**: `firmware_observed` (QEMU user-mode emulation) / `experimentally_confirmed` (verified on physical hardware)
- **`image_sha256`**: `N/A`
- **`target_sha256`**: `N/A`
- **`suite_sha256`**: `N/A`

## Semantic Verdict Diff

<!-- Paste the output of: python tools/verdict_diff.py --base origin/main --head HEAD -->

```markdown
No oracle suite TSV changes detected.
```

## Verification Checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy` passes with zero errors
- [ ] `pytest --cov` passes with 100.00% statement and branch coverage
- [ ] `python tools/guard.py` passes (no binary artifacts or vendor code)
- [ ] `python tools/mcp_index.py --check` and `python tools/sync_readme.py --check` pass
