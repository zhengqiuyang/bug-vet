---
name: bug-vet-bench
description: Use this skill when running ContractBench regression benchmarks across merged historical fixes or adding new benchmark cases without modifying source repositories.
---

# bug-vet ContractBench Regression Workflow

Evaluate structural and contract oracle recall and post-fix negative control across read-only `git archive` snapshots.

## Steps
1. **Run the full 12-case benchmark suite**:
   ```powershell
   bug-vet bench --cases D:\projects\bug-vet\bench\cases.json --json-out D:\projects\bug-vet\bench\reports\latest.json --markdown-out D:\projects\bug-vet\bench\reports\BENCHMARK.md
   ```
2. **Run a single case while iterating on an oracle rule**:
   ```powershell
   bug-vet bench --cases D:\projects\bug-vet\bench\cases.json --case <case-id>
   ```

## Validation
Confirm `summary.recall_at_5_count` does not regress below baseline (`10/12`, 83.3%) and `post_fix_cleared_count` equals `structural_hit_count` (`11/11`) with zero modifications to any benchmark source repository.
