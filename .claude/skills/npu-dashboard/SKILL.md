---
name: npu-dashboard
description: Regenerate the offline PyTorch NPU test-case dashboard from a raw results workbook (all_testcases.xlsx). Aggregates per-module file-level generalization and case-level execution results into index.html and writes the per-case detail tree to a sibling cases.js, keeping the HTML small even for hundreds of thousands of cases. Use when asked to regenerate or refresh the test dashboard, rebuild dashboard numbers, or produce the same dashboard from a new test-results Excel. Triggered by phrases like "生成看板", "刷新看板", "数据看板", "测试看板", "测试用例看板", "dashboard".
allowed-tools: Read, Write, Edit, Bash
---

# NPU Test-Case Dashboard Generator

Turn a raw results workbook into the offline HTML dashboard at
`/workspace/dashboard/index.html` (plus the sibling `cases.js` holding the
per-case details). One command does the whole thing — the rendering code
(charts, table, theme, detail tree) is fully data-driven and never needs editing
for a new sample; only the generated `DATA` and `cases.js` change.

## Quick Start

```bash
cd /workspace/dashboard
python3 .claude/skills/npu-dashboard/scripts/generate_dashboard.py \
    --input all_testcases.xlsx --output index.html
```

New data sample, same dashboard:

```bash
python3 .claude/skills/npu-dashboard/scripts/generate_dashboard.py \
    --input new_sample.xlsx --output index.html
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input`, `-i` | `all_testcases.xlsx` | Input workbook (schema auto-detected, see below) |
| `--output`, `-o` | `index.html` | HTML to write (the template with `__DATA__` markers) |
| `--cases-out`, `-c` | `<output dir>/cases.js` | Where to write the per-case detail JS |
| `--blacklist`, `-b` | `blacklist_testcases.xlsx` next to `--input` (if present) | Blacklist workbook; folds blacklisted cases into the case totals |
| `--status`, `-s` | `status_tracking.xlsx` or `summary_report.xlsx` next to `--input` (if present) | Tracking workbook (one sheet per module); attaches a status tag (`Done`/`Todo`/`In Progress`/`Backlog`, read from `Status` or `社区status`), a priority tag (`High`/`Medium`/`Low`/`Should Not Do`, from `Priority`), and the assignee (from `Assignee` or `author`) to each file in the 测试文件 tab |
| `--json-out` | _(none)_ | Optional: also dump the computed `DATA` to a `.json` file |

Dependencies: `openpyxl` only (`pip3 install openpyxl`).

## Input schema (the raw workbook)

Two layouts are supported, auto-detected by the sheet names present. Columns are
always located by header name (order-independent), with a positional fallback if
the header text differs.

### Current: dedicated `all_files` + `all_testcases` sheets

The module key is the **sheet name** (the original grouping), not the
`Classification` column. `Classification` is a finer sub-division which the
generator folds back onto its owning sheet via a `Classification → sheet` map
built from the per-module case sheets. The current schema splits Tensor across
separate `Tensor Operators` and `Tensor Types` sheets; both are canonicalized
back onto a single `Tensor` module (see `_canonical_module`).

- **`all_files`** — one row per test file (the file-level tier):
  | Column | Header | Meaning |
  |--------|--------|---------|
  | `Classification` | `Classification` | Fine sub-division; folded onto its sheet for the module key. Forward-filled (only the first row of each module group is set) |
  | `File` | `File` | Test file path |
  | `num` | `num` | Matched case count for that file (`0` → 未泛化) |

- **`all_testcases`** — one row per executed case (the case-level tier), every
  cell populated:
  | Column | Header | Meaning |
  |--------|--------|---------|
  | `Classification` | `Classification` | Fine sub-division; folded onto its sheet for the module key |
  | `File` | `File` | Test file path |
  | `nodeid` | `nodeid` | Concrete test-case id |
  | `result` | `执行结果` | `passed` / `failed` / `skipped` / `timeout` / `error` |

  Columns `Specialization`, `报错日志`, `skip原生日志首行`, `黑名单跳过`,
  `不支持`, `不支持原因` are present but not consumed. The per-module sheets
  (`Core`, `Tensor`, …) are the source of the `Classification → sheet` map and
  the `all_testcases` sheet is their union; the generator reads only
  `all_files` + `all_testcases` for the numbers.

### Blacklist (in-workbook `黑名单跳过` sheet, or legacy separate workbook)

Blacklisted/disabled cases are folded into the case-level tier (and the detail
tree). Two sources are supported, whichever is present:

- **Current schema** — a `黑名单跳过` sheet *inside* the input workbook. Preferred
  when present; a separate `--blacklist` file is only consulted when the input has
  no such sheet.
- **Legacy schema** — a sibling `blacklist_testcases.xlsx` (auto-detected next to
  `--input`, or passed via `--blacklist`) with a single `all_blacklist` sheet.

Both share the same columns (the in-workbook sheet also carries `skip来源` and
`issue`, which are ignored):

| Column | Header | Meaning |
|--------|--------|---------|
| `Classification` | `Classification` | Folded onto its sheet for the module key (forward-filled) |
| `File` | `File` | Test file path (forward-filled) |
| `nodeid` | `nodeid` | Concrete test-case id |
| `skip分类` | `skip分类` | `device not supported` / `cann not supported` / `cann_not_supported` / `dtype_not_supported` / `Running Skiped` (note the typo) |
| `skip原因` | `skip原因` | Human-readable skip reason (shown in the detail view) |

Each blacklisted case becomes `skipped` when its `skip分类` contains `running`
(case-insensitive — the legacy `Running Skiped` entries), otherwise the
`blacklist_unsupported` status. In the current schema the "Running Skiped" cases
moved out of the blacklist into `all_testcases` itself (as plain `skipped`), so
only the unsupported categories remain in the sheet. The `skip分类` and `skip原因`
are stored in the detail tree so the 用例详情 view can show them.

### Legacy: one sheet per module (fallback)

Used when `all_files`/`all_testcases` are absent. The sheet *name* is the module
key (e.g. `Core`, `Tensor`, `Distributed`, `Graph`, `Math`, `Quantization`,
`Utils`); `File`/`nodeid`/`执行结果` columns are as above. A `nodeid` of
`(未匹配)` (or empty) marks a **file-level** (未泛化) record whose `执行结果` is
`N/A`; otherwise the row is a case. `File` is forward-filled (merged-cell
convention — only the first row of each file group is filled).

## Transformation (the single source of truth for the numbers)

The script computes exactly the `DATA` object the dashboard renders. In the
current schema, `all_files` supplies the file-level tiers, `all_testcases` the
executed case-level rows, and the blacklist (in-workbook `黑名单跳过` sheet, or
legacy separate workbook) the blacklisted cases; module keys are the **sheet
names** (a `Classification → sheet` map folds the finer sub-divisions back onto
their sheet). The 2026-09-02 sample resolves to:

```text
files_total     = unique File values in all_files                 (1202)
files_gen       = unique File values with num > 0                 (126)
files_snd       = ungeneralized files whose Priority == "Should Not Do" (174)
files_na        = files_total - files_gen - files_snd             (902, 未泛化)
files_gen_rate  = files_gen / (files_gen + files_na) × 100, 1 dec (12.3%)

cases_total     = rows in all_testcases + blacklisted cases       (179066)
cases.passed|failed|timeout|error = executed rows by 执行结果
cases.skipped   = executed skipped rows (running-skip included)   (17589)
cases.blacklist_unsupported = blacklisted (disabled) cases        (16394)
cases_pass_rate = cases.passed / cases_total × 100, 1 decimal     (77.5%)

per module (grouped by sheet name):
  files        = unique File values in that module
  gen_files    = unique File values in that module with num > 0
  snd_files    = ungeneralized files in that module with Priority == "Should Not Do"
  na_files     = files - gen_files - snd_files
  gen_cases    = collected cases in that module (= sum of the 6 statuses)
  passed/failed/skipped/blacklist_unsupported/timeout/error = collected rows by status
```

> A file is generalized, needs generalization, or is marked 无需泛化 — it is never
> counted in more than one file-level tier. `na_files` always equals
> `files - gen_files - snd_files`; `sum(gen_files) == files_gen` and
> `sum(snd_files) == files_snd`, and `sum(num) == cases_total`. The 已泛化/未泛化
> split is driven purely by executed `num`; 无需泛化 is then the subset of 未泛化
> whose `Priority == "Should Not Do"`. So a file with *only* blacklisted cases
> (e.g. `test_jit.py`, 30 blacklist entries) still reads as 未泛化 at the file
> level while its blacklist cases appear in the case detail tree.

## How the two files are produced

The output is **two files** that sit side by side and work fully offline:

- **`index.html`** — the dashboard shell: all CSS/HTML/JS plus the small
  aggregate `DATA` object, injected inline between one pair of markers:

  ```js
  const DATA = /*__DATA_BEGIN__*/ { ...aggregate... } /*__DATA_END__*/;
  ```

- **`cases.js`** — the bulky per-case detail tree, written as a single
  `window.CASES = { ... }` assignment and loaded by the shell via
  `<script src="cases.js"></script>`. Its shape is
  `module -> file -> [[nodeid_suffix, result], ...]`; the nodeid's file prefix is
  stripped and stored once per file, and the UI reconstructs the full nodeid as
  `file + "::" + suffix`. Executed cases are 2-element `[suffix, result]`;
  blacklisted cases are 4-element `[suffix, result, skip分类, skip原因]` so the
  用例详情 view can show the skip reason. A second assignment
  `window.FILES = [[module, file, gen, cases, status, priority, assignee], ...]`
  (gen = 1/0, cases = executed + blacklisted; status/priority/assignee come from
  the tracking sheet, `""` when the file is absent) backs the 测试文件 tab. This keeps
  `index.html` tiny no matter how many cases there are — hundreds of thousands of
  cases grow `cases.js`, not the HTML.

The script serializes `DATA` to JSON (a valid JS object literal) and replaces
everything between the markers; `cases.js` is regenerated from scratch each run.
Everything outside the `DATA` markers — CSS, HTML, canvas renderers, theme
toggle, table, detail-view code — is static and reused as-is.

## Interactive behaviors (static shell, preserved by regeneration)

These live in `index.html`'s rendering code (outside the `DATA` markers), so a
regeneration leaves them unchanged:

- **Click-to-drill.** The case donut (slices + legend items), the 各模块用例执行结果
  stacked bar, and the 模块详情汇总 table all jump to the 用例详情 tab via a global
  bridge `window.openCaseDetails(status, module)`:
  - case-donut slice / legend item → filter by that result (`passed` / `failed` /
    `skipped` / `blacklist_unsupported` / `timeout_error`); the 超时/错误 slice
    maps to the combined `timeout_error` status.
  - stacked-bar status segment → filter by module + result; a module row's empty
    area → filter by module only.
  - module-summary table cell → the module name / 收集用例 cells filter by module
    only; a Passed/Failed/Skipped/Blacklist/Timeout/Error count filters by
    module + result;
    the 合计 row filters by the global (all-module) + result. Cells of zero-case
    modules are rendered plain (not clickable).
- **Details filters.** The details toolbar has four filters — text search
  (module/file/nodeid), a module `<select>`, a status `<select>` (with a
  combined `timeout_error` option), and a skip-category `<select>` (the distinct
  `skip分类` values, "全部 skip 类别" by default) — combined with AND. `openCaseDetails`
  sets the relevant ones before switching views.
- **测试文件 tab.** Groups every test file by module (a collapsible module node
  whose children are that module's files, each showing path followed by fixed-width
  trailing columns in order 泛化用例数 / assignee / status tag (Done/In
  Progress/Todo/Backlog) / priority tag (High/Medium/Low/Should Not Do) / 已泛化·未泛化
  badge — every column always rendered so they line up vertically, empty when a
  value is missing). Its toolbar has a text search, a module `<select>`, a
  gen-status `<select>` (全部/已泛化/未泛化/无需泛化), a status `<select>` (全部状态/Done/In
  Progress/Todo/Backlog/未跟踪), a priority `<select>` (全部优先级/High/Medium/Low/
  Should Not Do/无优先级), and an assignee `<select>` (全部负责人/…/未分配, populated
  from the distinct assignees), combined with AND.
  The file-level charts drill down into it via `window.openFilesTab(filter)`:
  - 文件泛化率 donut slice / legend item → filter by gen status (已泛化 / 未泛化 / 无需泛化).
  - 各模块文件泛化情况 bar segment → filter by module + gen status; a module row's
    grey (未泛化) area → filter by module only.
  - Clicking a generalized file row → `window.openCaseFile(module, file)`, which
    jumps to 用例详情 filtered to that file's cases.
- **Hover highlight (no border).** Hovering a donut slice pops it outward 5px while
  others dim to 30% opacity; hovering a stacked-bar segment dims the rest and bolds
  the module label. Applies to both the case charts and the file charts (文件泛化率
  donut + 各模块文件泛化情况 bar). State is transient
  (`casePieHover` / `moduleBarHover` / `fileDonutHover` / `fileBarHover`), cleared
  on mouseleave.

## Instructions

### Step 1: Confirm the input

Make sure the workbook path is right and its sheets/columns match the schema
above. If the sample uses different header text, extend `COLUMN_SPEC` in the
script rather than hardcoding positions.

### Step 2: Run the generator

```bash
cd /workspace/dashboard
python3 .claude/skills/npu-dashboard/scripts/generate_dashboard.py \
    --input all_testcases.xlsx --output index.html
```

The script prints a verification summary: totals, pass rate, blacklist total,
and one line per module (files / gen / na / cases / P / F / S / T / E / B).

### Step 3: Verify the output

Check against the printed summary:

- `files_gen + files_snd + files_na == files_total`, and sum of per-sheet `files == files_total`
- sum of per-sheet `gen_files == files_gen`, per-sheet `snd_files == files_snd`,
  and per-sheet `na_files == files - gen_files - snd_files`
- sum of per-sheet `gen_cases == cases_total`, and
  `passed+failed+skipped+blacklist_unsupported+timeout+error == cases_total`
- pass/fail rates round to the same values shown in the summary tiles

### Step 4: Sanity-check the HTML

Open `index.html` in a browser (works offline, no CDN; `cases.js` must be in the
same folder as `index.html`). Confirm:

- Summary strip: 测试文件 / 已泛化文件 / 收集用例 / 通过用例数 / 失败用例数
- Case-level: 用例执行结果分布 donut + 各模块用例执行结果 stacked bar + 模块详情汇总 table
  (columns 模块 / 文件 / 已泛化 / 收集用例 / Passed / Failed / Skipped / Blacklist / Timeout / Error / 通过率;
  sortable headers, no inline result-distribution bar)
- File-level: 文件泛化率 donut + 各模块文件泛化情况 stacked bar
- 用例详情 tab: drill-down 模块 → 文件 → 用例 (nodeid + 执行结果), with search / module / status /
  skip-category filters and chunked "加载更多" per file; blacklisted cases show their `skip分类`
  as a compact chip (the `skip原因` in its tooltip, keeping rows uncluttered)
- 测试文件 tab: files grouped by module (collapsible), each file showing path / 已泛化·未泛化
  badge / case count, with search, module, and gen-status filters
- Clicking a case-donut slice/legend, a module-bar segment, a 模块详情汇总 table cell, or a
  file-level donut/bar segment jumps to 用例详情 / 测试文件 with the matching filter; hovering
  highlights without a border
- Light/dark toggle re-renders with correct status colors

## Notes & edge cases

- **Do not load the workbook in `read_only` mode.** This workbook reports broken
  dimension metadata (`max_row=1`) in read-only mode, which silently truncates
  iteration to the header row. The script loads in normal mode on purpose.
- **Unknown execution-status values** (anything outside the 5 known keys) are
  folded into `error` with a `[warn]` line, so `gen_cases` always reconciles.
- **`top_specs` / `top_unsupported` are legacy.** Their charts were removed from
  the dashboard, so the generator no longer emits them. If those charts are
  re-added, derive them from the `Specialization` (grouped case counts) and
  `不支持原因` (grouped counts) columns and re-add them inside the markers.
- The dashboard is **two-tier by design**: 未泛化 entries are file-level only
  (no nodeid) and are excluded from all execution percentages.
