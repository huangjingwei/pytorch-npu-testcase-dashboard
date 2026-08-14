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
| `--input`, `-i` | `all_testcases.xlsx` | Input workbook (one sheet per module) |
| `--output`, `-o` | `index.html` | HTML to write (the template with `__DATA__` markers) |
| `--cases-out`, `-c` | `<output dir>/cases.js` | Where to write the per-case detail JS |
| `--json-out` | _(none)_ | Optional: also dump the computed `DATA` to a `.json` file |

Dependencies: `openpyxl` only (`pip3 install openpyxl`).

## Input schema (the raw workbook)

- **One sheet per module.** The sheet *name* becomes the module key (e.g.
  `Core`, `Tensor`, `Distributed`, `Graph`, `Math`, `Quantization`, `Utils`).
- **Row 1 is a header row.** Columns are located by name (order-independent),
  with a positional fallback if the header text differs:

| Column | Header | Meaning |
|--------|--------|---------|
| `File` | `File` | Test file path |
| `nodeid` | `nodeid` | Concrete test-case id, or `(未匹配)` when the file has no matched nodeid |
| `result` | `执行结果` | `passed` / `failed` / `skipped` / `timeout` / `error` / `N/A` |

  Columns `Classification`, `Specialization`, `2.7.1 失败`, `报错日志`,
  `不支持`, `不支持原因` are present but not consumed by the current dashboard.

- **Two tiers are encoded in one table** by the `nodeid` column:
  - `nodeid == "(未匹配)"` (or empty) → **file-level** record (未泛化), `执行结果` is `N/A`.
  - otherwise → **case-level** record with a real execution result.

## Transformation (the single source of truth for the numbers)

The script computes exactly the `DATA` object the dashboard renders:

```text
files_total     = unique File values across all sheets            (1205)
files_gen       = unique File values with ≥1 matched nodeid row   (43)
files_na        = files_total - files_gen                         (1162)
files_gen_rate  = files_gen / files_total × 100, 1 decimal        (3.6%)

cases_total     = count of matched rows                           (52091)
cases.passed|failed|skipped|timeout|error = matched rows by 执行结果
cases_pass_rate = cases.passed / cases_total × 100, 1 decimal     (68.1%)

per sheet:
  files        = unique File values in that sheet
  gen_files    = unique File values in that sheet with ≥1 matched row
  na_files     = files - gen_files
  gen_cases    = matched rows in that sheet (= sum of the 5 statuses)
  passed/failed/skipped/timeout/error = matched rows by 执行结果
```

> A file is either generalized or not — it is never counted in both tiers.
> `na_files` always equals `files - gen_files`.

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
  `file + "::" + suffix`. A second assignment
  `window.FILES = [[module, file, gen, cases], ...]` (gen = 1/0) backs the 测试文件
  tab. This keeps `index.html` tiny no matter how many cases there are — hundreds
  of thousands of cases grow `cases.js`, not the HTML.

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
    `skipped` / `timeout_error`); the 超时/错误 slice maps to the combined
    `timeout_error` status.
  - stacked-bar status segment → filter by module + result; a module row's empty
    area → filter by module only.
  - module-summary table cell → the module name / 泛化用例 cells filter by module
    only; a Passed/Failed/Skipped/Timeout/Error count filters by module + result;
    the 合计 row filters by the global (all-module) + result. Cells of zero-case
    modules are rendered plain (not clickable).
- **Details filters.** The details toolbar has three filters — text search
  (module/file/nodeid), a module `<select>`, and a status `<select>` (with a
  combined `timeout_error` option) — combined with AND. `openCaseDetails` sets the
  relevant ones before switching views.
- **测试文件 tab.** Groups every test file by module (a collapsible module node
  whose children are that module's files, each showing path + 已泛化/未泛化 badge +
  case count for generalized files). Its toolbar has a text search, a module
  `<select>`, and a gen-status `<select>` (全部/已泛化/未泛化), combined with AND.
  The file-level charts drill down into it via `window.openFilesTab(filter)`:
  - 文件泛化率 donut slice / legend item → filter by gen status (已泛化 / 未泛化).
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

The script prints a verification summary: totals, pass rate, and one line per
module (files / gen / na / cases / P / F / S / T / E).

### Step 3: Verify the output

Check against the printed summary:

- `files_gen + files_na == files_total`, and sum of per-sheet `files == files_total`
- sum of per-sheet `gen_files == files_gen`, per-sheet `na_files == files - gen_files`
- sum of per-sheet `gen_cases == cases_total`, and `passed+failed+skipped+timeout+error == cases_total`
- pass/fail rates round to the same values shown in the summary tiles

### Step 4: Sanity-check the HTML

Open `index.html` in a browser (works offline, no CDN; `cases.js` must be in the
same folder as `index.html`). Confirm:

- Summary strip: 测试文件 / 已泛化文件 / 泛化用例 / 通过用例数 / 失败用例数
- Case-level: 用例执行结果分布 donut + 各模块用例执行结果 stacked bar + 模块详情汇总 table
  (columns 模块 / 文件 / 已泛化 / 泛化用例 / Passed / Failed / Skipped / Timeout / Error / 通过率;
  sortable headers, no inline result-distribution bar)
- File-level: 文件泛化率 donut + 各模块文件泛化情况 stacked bar
- 用例详情 tab: drill-down 模块 → 文件 → 用例 (nodeid + 执行结果), with search / module / status
  filters and chunked "加载更多" per file
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
