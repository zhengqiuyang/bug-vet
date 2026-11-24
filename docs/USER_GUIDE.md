# `bug-vet` 使用手册（v0.4.0）

> **无最小可执行红/绿复现，不出报告；无结构化证伪，不发结论。**

`bug-vet` 是一个零运行时第三方依赖（纯 Python 3.10+ 标准库）的**契约型静默 Bug 挖掘与验证 Harness**。它既可以通过 **CLI** 独立驱动，也可以通过 **stdio MCP Server（9 个工具）** 接入 Claude Code、Cursor、Codex 等 AI Coding Agent，负责在开源库或本地工程里执行：

`结构化扫描 / 文档契约抽取 → 升格入账 → 对抗式证伪与 Playbook 对照 → 隔离探针红灯复现 & 最小化 → 修复后红转绿验证 → Fail-Closed 门禁 → Markdown / JSON / SARIF 报告`。

---

## 目录

1. [核心设计哲学（必读）](#1-核心设计哲学必读)
2. [安装与环境自检](#2-安装与环境自检)
3. [方式 A：接入 AI Coding Agent（MCP 模式）](#3-方式-a接入-ai-coding-agentmcp-模式)
4. [方式 B：CLI 完整 7 步实战工作流](#4-方式-bcli-完整-7-步实战工作流)
5. [CLI 全量命令速查表](#5-cli-全量命令速查表)
6. [探针（Probe）编写规范与防崩溃约定](#6-探针probe编写规范与防崩溃约定)
7. [对抗式证伪与假绿测试审计](#7-对抗式证伪与假绿测试审计)
8. [ContractBench 基准回测](#8-contractbench-基准回测)
9. [常见问题与本地安全边界](#9-常见问题与本地安全边界)

---

## 1. 核心设计哲学（必读）

1. **发现器（Oracle）≠ 裁判（Verifier）**：
   `scan` 只负责产**线索（candidates）**，永远不能直接当 Bug 报出去；只有写进探针并真正跑出可控失败、或者被 `falsify` 证伪并说明原因的项，才能通过门禁。
2. **Fail-Closed（失败即拦截）**：
   - 探针必须定义顶层 `def self_check()`，否则即使跑通也被判为 `unknown`（`missing-self-check`）。
   - 未捕获 Python `Traceback` / `SyntaxError` 的崩溃退出会被 `runner` 自动降级为 `ERROR`（记为 `unknown` 阻塞项），绝不会混充成已证实 Bug 的 `FAIL`。
   - 任何未决项（`active` / `unknown`）存在时，`gate` 只能给出 `partial` 或 `not-ready`，无法变成 `READY`。
3. **证据权威阶梯**：
   `doc < probe < test < published < runtime` —— 如果一条规则强制要求仓库测试级（`test`）证据，仅靠独立探针（`probe`）无法关闭它。

---

## 2. 安装与环境自检

推荐直接以可编辑模式安装到本地独立 `.venv`：

```powershell
# 创建/使用虚拟环境并安装（零运行时依赖；[dev] 仅用于跑 pytest）
pip install -e D:\projects\bug-vet
# 或含测试依赖：
pip install -e "D:\projects\bug-vet[dev]"

# 验证 CLI 可用
bug-vet --help
bug-vet playbook --list
```

> **Windows PowerShell 注意事项**
> - 带引号的可执行文件路径必须前缀 `&`，例如：
>   `& "D:\projects\bug-vet\.venv\Scripts\bug-vet.exe" playbook --list`
> - 控制台编码：`bug-vet` 核心库与 stdio MCP 均强制 UTF-8 二进制读写，避免中文 Windows 默认 cp936 乱码。
> - 运行产物隔离：建议始终加 `--runs-dir D:\projects\_bench\runs`（或设置环境变量 `BUG_VET_RUNS_DIR`），避免在被测开源仓库根目录生成 `.bug-vet/` 脏文件。

---

## 3. 方式 A：接入 AI Coding Agent（MCP 模式）

### 一键配置 MCP 客户端

```powershell
# 1. 先预览将写入哪些配置（安全，不写盘）
bug-vet setup --client all

# 2. 确认后应用配置（自动备份原有配置文件为 .bak）
bug-vet setup --client all --apply --project-dir path\to\project --command-bin "D:\projects\bug-vet\.venv\Scripts\bug-vet.exe"
```

支持的 `--client`：`claude`、 `cursor`、 `codex`、 `dsh`、 `antigravity`、 `all`。
也可以直接由客户端拉起 `bug-vet mcp`（纯 stdio JSON-RPC 2.0，无需额外依赖包）。

### 一键安装原生 Antigravity Plugin（v0.4.0 新增）

除了单独挂载 MCP，`bug-vet` 还内置了一整套原生 Antigravity Plugin（源码位于 [plugin/](file:///D:/projects/bug-vet/plugin)），一键安装即同时启用：

- **MCP 托管（`mcp_config.json`）**：自动注册 `bug-vet-mcp`（9 个 `bv_*` 工具）；
- **3 个渐进加载 Skills（`skills/`）**：`bug-vet-hunt`、`bug-vet-verify`、`bug-vet-bench`；
- **2 个物理隔离子 Agent（`agents/`）**：`bug-vet-hunter`（发现 + 写探针）与只读的 `bug-vet-falsifier`（唱反调 + 查文档/补偿链 + 假绿审计）；
- **常驻准则（`rules/AGENTS.md`）**：全局约束“无 `self_check()` 失败复现不出报告”；
- **2 道 `PreToolUse` 安全 Hooks（`hooks.json`）**：
  1. `bug-vet-probe-vacuity-guard`：在 `write_file` 写入 `probes/*.py` 前拦截空洞探针、语法错误或缺少 `def self_check()` 的脚本；
  2. `bug-vet-local-guard`：拦截 `git push` / `gh pr create` / `twine upload` / `uv publish`，强制弹窗确认（除非显式设置 `BUG_VET_ALLOW_REMOTE=1`）。

全局安装到本机 Antigravity 目录：

```powershell
D:\projects\bug-vet\.venv\Scripts\bug-vet.exe setup `
  --client antigravity --apply `
  --plugin-dir "$env:USERPROFILE\.gemini\config\plugins\bug-vet" `
  --command-bin "D:\projects\bug-vet\.venv\Scripts\bug-vet.exe" `
  --python-bin "D:\projects\bug-vet\.venv\Scripts\python.exe"
```

安装完成后无需重启，下一轮对话会自动加载，也可以在 Antigravity Customizations 页面随时启停。

### 9 个内置 MCP 工具一览

| MCP 工具名 | 典型参数 / 动作 | 作用 |
| :--- | :--- | :--- |
| `bv_scan` | `mode`: `all` / `siblings` / `parser_meta` / `contracts` / `claims` / `roundtrip` / `examples` / `callsites` | 运行结构化发现 Oracle 或文档契约抽取 |
| `bv_playbook` | `action`: `list` / `get`, `query` | 检索 5 套常见 Bug 家族 Playbook（含探针配方与证伪坑点） |
| `bv_ledger` | `action`: `show` / `add_obligation` / `promote_candidate` / `falsify` | 查看账本、录入人工 obligation、将扫描候选升格入账或记录证伪 |
| `bv_probe` | `action`: `scaffold` / `run` | 生成带 `self_check()` 的探针骨架，或在目标环境执行全部探针 |
| `bv_falsify` | `mode`: `compensation` / `vacuity` / `record` | 上游环境变量补偿链追踪、探针/测试假绿 AST 审计、写入证伪 |
| `bv_shrink` | `run`, `script`, `input` (JSON) | 基于 `BUG_VET_INPUT` 自动 Delta-Debug 缩减最小反例 |
| `bv_verify` | `mode`: `baseline` / `verify` / `test_evidence` / `diff` | 基线快照、修复前后红转绿对比、仓库单测权威验证、PyPI 发布版差分 |
| `bv_gate` | `action`: `gate` / `collide` / `issues` | Fail-Closed 门禁裁决，及（联机可选）GitHub Issue/PR 防撞车检查 |
| `bv_report` | `formats`: `["markdown","json","sarif"]`, `include_pr`, `include_candidates` | 输出 `REPORT.md`、`findings.json`、SARIF 2.1.0 `results.sarif` 与 `PR.md` |

---

## 4. 方式 B：CLI 完整 7 步实战工作流

假设我们要只读扫描本地某个仓库 `D:\projects\target-repo`，并把产物放在 `D:\projects\_bench\runs`：

### 第 1 步：全量结构化 + 契约扫描

```powershell
bug-vet scan --mode all D:\projects\target-repo --name myrun --runs-dir D:\projects\_bench\runs
```

这一步会依次执行：
- **三大结构化 Oracle**：`siblings`（ABC 子类 / `<entity>_to_<provider>` / `*_API_BASE` 环境分支 / TS 命令校验器不对称）、`parser_meta`（代码块围栏乱剥 / SSE 换行符不全 / 未转义正则 / 不安全 `.get().items()`）、`contracts`（`Sequence`/`Mapping` 窄化、重注册自毁、`coordinator.data[key]` 未防护下标）；
- **上游补偿自动过滤**：自动沿调用图检查 env 候选是否已在调用方完成补偿；
- **`claims` 文档/注释契约挖掘**；
- 输出去重排序后的 `candidates.json`。

### 第 2 步：对照 Playbook 与源码分诊，升格目标候选

先看对应的 Playbook 避免常见误报坑：

```powershell
bug-vet playbook --list
bug-vet playbook sibling-asymmetry
```

人工核查 `candidates.json` 中的高优候选，把确认需要调查的条目录入 `ledger.json`（无需手写 JSON）：

```powershell
# 方式 1：按 1-based 排名编号升格指定的候选（可重复传多个 --promote）
bug-vet ledger myrun --runs-dir D:\projects\_bench\runs --promote 1 --promote 3

# 方式 2：一次性升格所有非 claims 的结构化候选
bug-vet ledger myrun --runs-dir D:\projects\_bench\runs --promote-structural

# 方式 3：手工录入自己发现的任意契约 obligation
bug-vet ledger myrun --runs-dir D:\projects\_bench\runs `
  --add "ModelResponse preserves raw_usage when preserve_raw_usage=True" `
  --id my-custom-claim --source D:\projects\target-repo\pkg\model.py --line 120 --family sibling-asymmetry
```

### 第 3 步：生成并编写独立探针（Probe）

```powershell
bug-vet new-probe myrun --runs-dir D:\projects\_bench\runs `
  --claim my-custom-claim --family sibling-asymmetry `
  --description "Verify behavior across boundary inputs"
```

用编辑器完善生成的 `probes/probe_my-custom-claim_sibling-asymmetry.py`（见第 6 节模板），确保不 mock 被测函数，且带真实的 `self_check()` 正反例校验。

### 第 4 步：执行假绿静态审计 + 目标环境实测

```powershell
# 先用 AST 审计探针本身是否空洞
bug-vet falsify --check-vacuity D:\projects\_bench\runs\myrun\probes\probe_my-custom-claim_sibling-asymmetry.py --target-symbol get_response

# 在目标仓库的 Python 环境与工作目录下跑探针（不修改目标仓库任何文件）
bug-vet probe myrun --runs-dir D:\projects\_bench\runs `
  --python D:\projects\target-repo\.venv\Scripts\python.exe `
  --cwd D:\projects\target-repo
```

### 第 5 步：处理结论（证伪误报 OR 收缩反例 + 基线快照）

- **情况 A：探针 `PASS`（契约成立，无 Bug）** —— 账本会自动设为 `probed-pass`。
- **情况 B：源码/文档核实是误报或已知文档限制** —— 显式记录证伪原因，保证审计可追溯：

```powershell
bug-vet falsify myrun --runs-dir D:\projects\_bench\runs `
  --obligation ob-cand-1-xxxxxx `
  --category documented-limitation `
  --location "pkg/model.py:120" `
  --reason "Confirmed behavior matches documented caveat in docs/usage.md"
```

- **情况 C：探针真实 `FAIL`（命中静默 Bug）**：
  - 如需把复杂 JSON 输入缩成最小复现：
    ```powershell
    bug-vet shrink myrun --runs-dir D:\projects\_bench\runs `
      --script D:\projects\_bench\runs\myrun\probes\probe_xxx.py `
      --input D:\projects\_bench\runs\myrun\bad_input.json `
      --python D:\projects\target-repo\.venv\Scripts\python.exe --cwd D:\projects\target-repo
    ```
  - 如要继续修代码并拿红转绿证据：先打修复前基线，修改修复后跑 `verify`（以及仓库自有 pytest）：
    ```powershell
    bug-vet baseline myrun --runs-dir D:\projects\_bench\runs --python ... --cwd D:\projects\target-repo
    # ...应用修复补丁...
    bug-vet verify myrun --runs-dir D:\projects\_bench\runs --python ... --cwd D:\projects\target-repo
    bug-vet test-evidence myrun --runs-dir D:\projects\_bench\runs --obligation ob-my-custom-claim --cmd "pytest tests/test_model.py" --cwd D:\projects\target-repo
    ```

### 第 6 步：Fail-Closed 门禁裁决

```powershell
bug-vet ledger myrun --runs-dir D:\projects\_bench\runs --all
bug-vet gate myrun --runs-dir D:\projects\_bench\runs
# 如果你已完成红转绿修复验证，加上严格校验：
# bug-vet gate myrun --runs-dir D:\projects\_bench\runs --require-verification
```

只有当所有 `obligation` 均已收敛（无 `active`、`unknown`、环境变更或缺失 `self_check()`）时，门禁才会输出 **`gate: READY`**。

### 第 7 步：导出 Markdown / JSON / SARIF 2.1.0 报告

```powershell
bug-vet report myrun --runs-dir D:\projects\_bench\runs --formats markdown,json,sarif --include-candidates
```

生成文件位于运行目录内：
- `REPORT.md`：人类可读审计结论（确认 Bug、红绿验证、最小复现、已证伪假设、覆盖边界）
- `findings.json`：全量结构化 JSON 结论
- `results.sarif`：标准 **SARIF 2.1.0** 文件（确认 Bug 为 `error`，尚未人工结案的剩余结构化候选标注为 `warning`，**已在账本里 `probed-pass` / `probed-fail` / `falsified` 的候选不会重复吐出噪声 `warning`**）
- 如加上 `--pr`，还会生成 `PR.md` 摘要（仅用于本地记录整理）

---

## 5. CLI 全量命令速查表

> 所有读写 `<name>` 的命令均支持在子命令前或后传入 `--runs-dir <dir>`。

| 命令 | 核心参数 | 用途 |
| :--- | :--- | :--- |
| `bug-vet init <name>` | `--runs-dir` | 创建空运行目录与 `ledger.json` |
| `bug-vet scan <path> --name <run>` | `--mode {all,siblings,parser_meta,contracts,claims,roundtrip,examples,callsites}`、`--tier {A,B,all}`、`--no-ts`、`--no-falsifier`、`--register-obligations` | 运行发现扫描（支持 Markdown 文档内代码例批量执行 `--mode examples` 及调用点参数覆盖检查 `--mode callsites --function <fn>`） |
| `bug-vet playbook [query]` | `--list`、`--get <id>`、`--json` | 浏览或检索 5 套 Bug-Family Playbook |
| `bug-vet ledger <name>` | `--all`、`--json`、`--promote N`、`--promote-structural`、`--add TEXT [--id --source --line --symbol --family --required]` | 查看、从 `candidates.json` 升格候选或手工录入 obligation |
| `bug-vet new-probe <name>` | `--claim <id>`、`--family <f>`、`--out <path>` | 从模板生成带 `self_check()` 的独立探针脚本 |
| `bug-vet probe <name>` | `--python`、`--cwd`、`--env K=V`、`--timeout` | 在指定环境执行 `probes/*.py` 并按权威度入账 |
| `bug-vet falsify` | 1) `--check-compensation <repo> --symbol S [--env-var V]`<br>2) `--check-vacuity <file.py> [--target-symbol S]`<br>3) `<name> --obligation OB --reason R [--category C --location L]` | 上游补偿链分析 / 探针或测试假绿审计 / 记录已证伪项 |
| `bug-vet shrink <name>` | `--script <py> --input <json> [--max-runs 300]` | 通过环境变量 `BUG_VET_INPUT` 自动最小化反例 JSON |
| `bug-vet baseline <name>` | `--python`、`--cwd`、`--env` | 保存修复前的探针失败基线 |
| `bug-vet verify <name>` | `--python`、`--cwd`、`--env` | 验证修复后红灯探针转绿且无回归 |
| `bug-vet test-evidence <name>` | `--obligation OB --cmd "..."` | 执行仓库官方测试获取 `test` 级别权威证据 |
| `bug-vet diff <name>` | `--scenario S --inputs J (--old-python ... --new-python ... \| --pip-package P --pip-version V)` | 跨本地解释器或 PyPI 已发布版本差分行为对比 |
| `bug-vet gate <name>` | `--require-verification`、`--require-collision` | 执行 Fail-Closed 门禁裁决，输出 `gate.json` |
| `bug-vet issues <owner/repo>` | `--label`、`--limit`、`--show` | 列出尚未被认领/关联的开放 Issue（调用本地 `gh` CLI） |
| `bug-vet collide <owner/repo>` | `--issue N`、`--keyword`、`--file`、`--require-clear` | 检查 Issue 时间线与开放 PR 是否撞车（调用本地 `gh` CLI） |
| `bug-vet report <name>` | `--formats markdown,json,sarif`、`--pr`、`--include-candidates` | 生成报告与 SARIF 文件 |
| `bug-vet bench` | `--cases`、`--case`、`--json-out`、`--markdown-out` | 运行 ContractBench 回归评测 |
| `bug-vet setup` | `--client {all,claude,cursor,codex,dsh,antigravity}`、`--apply`、`--project-dir`、`--plugin-dir`、`--python-bin` | 生成 MCP 配置或安装原生 Antigravity Plugin |
| `bug-vet mcp` | 无 | 启动零依赖 stdio MCP Server |
| `bug-vet status <name>` | `--runs-dir` | 查看某个 run 目录下已产出的所有工件 |

---

## 6. 探针（Probe）编写规范与防崩溃约定

每个探针都是**零外部测试框架依赖的独立 Python 脚本**，这样可直接用被测仓库自身的 `.venv/Scripts/python.exe` 运行，无需向目标环境安装 `pytest` 或 `bug-vet`。

### 退出码约定（`runner.py` 强制执行）

| 退出码 | 含义 | `bug-vet` 处理方式 |
| :---: | :--- | :--- |
| `0` | `PASS`（契约成立，无 Bug） | 计为 `probed-pass` |
| `1` | `FAIL`（**且 stderr 不含未捕获 `Traceback`/`SyntaxError`**） | 计为 `probed-fail`（真正确认契约违背） |
| `1`（带未捕获 `Traceback`） | 探针脚本本身写崩了（如 `TypeError`、`ImportError`） | **自动降级为 `ERROR` → 记作 `unknown`，阻塞门禁，绝不伪造 Bug** |
| `2` | `INCONCLUSIVE`（缺少前置依赖/无法构造目标条件） | 记作 `unknown`，阻塞门禁 |
| `3` | `CHECKER_BUG`（`self_check()` 自身失败） | 记作 `unknown`，阻塞门禁 |

### 标准探针写法要点

1. 顶部绑定声明必须与账本一致：`CLAIM = "<claim-id>"`（或 `OBLIGATION = "ob-<claim-id>"`）。
2. `self_check()` 必须同时校验“好输入放行 + 坏输入拦下”，不能空函数直接返回 `[]`。
3. 在 `check(failures)` 里向传入的 `failures` 列表 `.append(...)` / `.extend(...)` / `+= [...]`；不要 patch/mock 被测函数本身，只允许用隔离 transport 或临时目录驱动它。

可直接套用的最小骨架：

```python
from __future__ import annotations

CLAIM = "my-claim-id"

def check_invariant(observed: object, expected: object) -> bool:
    return observed == expected

def self_check() -> list[str]:
    problems: list[str] = []
    if not check_invariant("ok", "ok"):
        problems.append("check_invariant rejected equal pair")
    if check_invariant("ok", "broken"):
        problems.append("check_invariant accepted mismatched pair")
    return problems

def check(failures: list[str]) -> None:
    # 1. 正向对照输入
    # 2. 边界/破坏性输入，失败时记录原因：
    # if ...:
    #     failures.append("detailed discrepancy")
    pass

def main() -> int:
    problems = self_check()
    if problems:
        print("CHECKER BUG: " + "; ".join(problems))
        return 3
    failures: list[str] = []
    check(failures)
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: invariant verified")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

---

## 7. 对抗式证伪与假绿测试审计

### 上游补偿检查（针对 `env-asymmetry` 候选）

当分支里出现“读取 `X_API_KEY` 但未读取 `X_API_BASE`”时，先用 AST 调用图往上追溯 2-3 层，防止把“上游路由早已解析妥当”的代码误报成 Bug：

```powershell
bug-vet falsify --check-compensation D:\projects\target-repo --symbol target_fn --env-var X_API_BASE --max-depth 3
```

### 假绿 AST 审计（针对探针与单元测试）

自动检测 6 类无效/造假测试：`no-assertions`、`tautology-assert`、`swallowed-assertion`、`loose-caplog-text`、`target-mocked`、`empty-self-check`：

```powershell
bug-vet falsify --check-vacuity path\to\probe_or_test.py --target-symbol target_fn
```

---

## 8. ContractBench 基准回测

内置 12 个真实开源仓库历史 Bug 的前后对比评测集（[bench/cases.json](file:///D:/projects/bug-vet/bench/cases.json)），全程通过 `git archive` 提取只读树到临时缓存目录，不改动源仓库：

```powershell
# 全量跑 12 个基准 case
bug-vet bench --cases D:\projects\bug-vet\bench\cases.json `
  --json-out D:\projects\bug-vet\bench\reports\latest.json `
  --markdown-out D:\projects\bug-vet\bench\reports\BENCHMARK.md

# 只跑单个 case 验证规则改动
bug-vet bench --cases D:\projects\bug-vet\bench\cases.json --case mujoco-rollout-sequence
```

---

## 9. 常见问题与本地安全边界

- **只读目标仓库**：`scan`、`falsify`、`probe` 与 `bench` 均不写入被测仓库；配合 `--runs-dir` 可将所有产物集中到独立目录（例如 `D:\projects\_bench\runs`）。
- **勿发无证结论**：只要探针因环境缺失或代码问题抛出未捕获 Traceback，现在一律记为 `ERROR` → `unknown`，请先修探针环境再重跑，不要强行跳过门禁。
- **不自动联网推送/提 PR**：`issues` 与 `collide` 为只读查询（调用本机 `gh`）；`bug-vet` 不包含任何 `git push` 或 `gh pr create` 写操作。
