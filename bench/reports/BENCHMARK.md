# ContractBench Results

- **Cases evaluated**: 12
- **Pre-fix Recall@5**: 10/12 (83.3%)
- **Pre-fix Top-1 hits**: 7/12
- **Post-fix cleared (structural hits)**: 11/11
- **Total env false-positives filtered by falsifier**: 0

| # | Case | Repo | Pre-fix Hit | Rank | Firing Oracle(s) | Total Cands | Post-fix Cleared |
| :-: | :--- | :--- | :---: | :---: | :--- | :---: | :---: |
| 1 | `instructor-null-image-data` | `instructor` | **HIT** | 1 | `siblings` | 2 | YES (-1) |
| 2 | `litellm-cohere-env-asymmetry` | `litellm` | **HIT** | 1 | `claims, siblings` | 6 | YES (-1) |
| 3 | `pragent-provider-line-counts` | `pra` | **MISS (>5)** | 8 | `siblings` | 22 | YES (-3) |
| 4 | `pragent-fence-strip` | `pra` | **HIT** | 2 | `claims, parser_meta` | 11 | YES (-2) |
| 5 | `headroom-sse-separators` | `headroom` | **HIT** | 1 | `parser_meta` | 85 | YES (-1) |
| 6 | `deeptutor-footnote-markers` | `DeepTutor` | **HIT** | 3 | `claims, parser_meta` | 22 | YES (-1) |
| 7 | `haystack-pipeline-non-mapping` | `haystack` | **HIT** | 1 | `claims, parser_meta` | 24 | YES (-1) |
| 8 | `haystack-splitter-bounds` | `haystack` | **MISS (>5)** | 6 | `claims` | 22 | - (claims-only) |
| 9 | `mujoco-rollout-sequence` | `mujoco` | **HIT** | 1 | `contracts` | 18 | YES (-1) |
| 10 | `ha-omada-missing-key` | `home-assistant-core` | **HIT** | 1 | `contracts` | 6 | YES (-1) |
| 11 | `headroom-ws-reregister` | `headroom` | **HIT** | 2 | `claims, contracts` | 85 | YES (-1) |
| 12 | `testsprite-testlist-junit` | `testsprite-cli` | **HIT** | 1 | `siblings` | 1 | YES (-1) |
