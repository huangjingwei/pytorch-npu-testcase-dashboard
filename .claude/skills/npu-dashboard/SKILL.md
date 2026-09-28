---
name: npu-dashboard
description: Regenerate the offline TorchNPU 社区用例看板 (PyTorch NPU community test-case dashboard) from a raw results workbook (all_testcases.xlsx). Aggregates per-module file-level generalization and case-level execution results into index.html and writes the per-case detail tree to sibling cases_*.js files, keeping the HTML small even for hundreds of thousands of cases. Use when asked to regenerate or refresh the test dashboard, rebuild dashboard numbers, or produce the same dashboard from a new test-results Excel. Triggered by phrases like "生成看板", "刷新看板", "数据看板", "测试看板", "测试用例看板", "dashboard".
allowed-tools: Read, Write, Edit, Bash
---

# NPU Test-Case Dashboard Generator

Turn a raw results workbook into the offline HTML dashboard at
`/workspace/dashboard/index.html` (plus the sibling `cases_*.js` holding the
per-case details). One command does the whole thing — the rendering code
(charts, table, theme, detail tree) is fully data-driven and never needs editing
for a new sample; only the generated `DATA` objects and `cases_*.js` change.

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

## Datasets & scenario switch (2-D grid: A3/A5 reports × 总量/解耦 scenarios)

`index.html` carries **four** aggregate `DATA` objects inline — one per
(report, scenario) pair — plus four case detail files:

| Report | Scenario | `DATA` object | cases file |
|---|---|---|---|
| A3 | 总量用例 | `DATA_A3_TOTAL` | `cases_a3_total.js` |
| A3 | 解耦用例 | `DATA_A3_DECOUPLE` | `cases_a3_decouple.js` |
| A5 | 总量用例 | `DATA_A5_TOTAL` | `cases_a5_total.js` |
| A5 | 解耦用例 | `DATA_A5_DECOUPLE` | `cases_a5_decouple.js` |

Each cases file sets the same `window.CASES` / `window.FILES` globals. A header
segmented control (A3 报告 / A5 报告) selects the **report**; the two 场景 tabs
(总量用例 / 解耦用例) select the **scenario** under it. Both switches set transient
URL params — `?report=A3|A5` and `?scenario=TOTAL|DECOUPLE` — and reload, each
preserving the other dimension. On load a small bootstrap script reads both params
and `document.write`s the matching `<script src>` (e.g. `cases_a3_total.js`), so
only the active pair's 25–50 MB detail file is parsed, then strips the params from
the URL (no `localStorage`, so a plain refresh always returns to the default
A3 · 总量用例). The rendering code is otherwise unchanged — it always reads the
fixed names `DATA` / `window.CASES` / `window.FILES`.

Regenerate a report whenever a new sample lands (A3 shown; A5 omits `--total`
until an A5 总量 sample exists):

```bash
python3 .claude/skills/npu-dashboard/scripts/generate_dashboard.py \
    --input <a3-decouple>/all_testcases.xlsx --output index.html --dataset A3 --date 2026-09-26 \
    --total <a3-total>/all_testcases.xlsx --total-date 2026-09-23
python3 .claude/skills/npu-dashboard/scripts/generate_dashboard.py \
    --input <a5-decouple>/all_testcases.xlsx --output index.html --dataset A5 --date 2026-09-26
```

Run A3 (with `--total`) *and* A5 — each run only rewrites its own report's
`DATA_<R>_*` objects and cases files, leaving the other report as-is.
`DATA.date`/`DATA.dataset` feed the header subtitle and footer.

> **Default view is A3 · 总量用例.** The bootstrap falls back to `report=A3`,
> `scenario=TOTAL` when either param is missing (and maps unrecognised values to
> those defaults). The params are transient — stripped from the URL after the
> bootstrap reads them — so a plain refresh always lands on A3 · 总量用例 rather
> than remembering the last tab. The header A3/A5 toggle and the two 场景 tabs
> are independent: switching the report keeps the current scenario, and switching
> the scenario keeps the current report.

> As of 2026-09 the A5 report is kept but blanked: `DATA_A5_TOTAL` and
> `DATA_A5_DECOUPLE` are `null` and `cases_a5_total.js`/`cases_a5_decouple.js` hold
> only `window.CASES={};window.FILES=[];`. The shell's `DATA_EMPTY` guard renders a
>「暂无数据」empty state whenever the active (report, scenario) has no data, so A5
> shows placeholder until a fresh A5 sample is regenerated.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input`, `-i` | `all_testcases.xlsx` | Input workbook (schema auto-detected, see below) |
| `--output`, `-o` | `index.html` | HTML to write (the template with `__DATA__` markers) |
| `--cases-out`, `-c` | `<output dir>/cases_<report>_decouple.js` | Where to write the per-case detail JS |
| `--blacklist`, `-b` | `blacklist_testcases.xlsx` next to `--input` (if present) | Blacklist workbook; folds blacklisted cases into the case totals |
| `--status`, `-s` | `status_tracking.xlsx` or `summary_report.xlsx` next to `--input` (if present) | Tracking workbook (one sheet per module); attaches a status tag (`Done`/`Todo`/`In Progress`/`Backlog`, read from `Status` or `社区status`), a priority tag (`High`/`Medium`/`Low`/`Should Not Do`, from `Priority`), and the assignee (from `Assignee` or `author`) to each file in the 测试文件 tab |
| `--json-out` | _(none)_ | Optional: also dump the computed `DATA` to a `.json` file |
| `--dataset`, `-d` | `A3` | Report key (`A3`/`A5`). Regenerates that report's **both** scenarios: 解耦用例 (`DATA_<R>_DECOUPLE` between `__DATA_<R>_DECOUPLE_BEGIN__/END__` + `cases_<r>_decouple.js`) and — when `--total` is given — 总量用例 (`DATA_<R>_TOTAL` + `cases_<r>_total.js`) |
| `--date` | _(none)_ | Report date string stored in `DATA.date` and shown in the header/footer (e.g. `2026-09-19`) |
| `--total` | _(none)_ | The report's Total (总量) workbook. Builds the 总量用例 scenario — `DATA_<R>_TOTAL` (between `__DATA_<R>_TOTAL_BEGIN__`/`__DATA_<R>_TOTAL_END__`) plus `cases_<r>_total.js` — with the 看护策略 numbers from `build_total()` folded into the scenario object as `DATA_<R>_TOTAL.watch` (the top 「用例总览」 card and 解耦进度 target both read this). Omitted, `DATA_<R>_TOTAL` stays `null` and a blank `cases_<r>_total.js` is written so the 总量用例 tab never 404s |
| `--total-status` | `summary_report.xlsx` next to `--total` (if present) | Tracking workbook for the Total data. Resolves `Should Not Do` (Priority) into the SND 文件集合: their cases are excluded from the 总量用例 scenario's case counts **and** from the 看护策略 `社区跳过用例`/`黑名单跳过用例` terms (so `目标看护用例` == `passed+failed+timeout+error`); also attaches status/priority/assignee to the 总量用例 测试文件 tab |
| `--total-date` | `--date` | Report date for the Total scenario (`DATA_<R>_TOTAL.date`; e.g. `2026-09-23` while the A3 `--date` is `2026-09-26`) |

Dependencies: `openpyxl` only (`pip3 install openpyxl`).

## 用例总览 (top of page, from the Total workbook)

The page opens with a fixed 「用例总览」 section fed by the **current report's**
Total (总量) workbook (via `--total`) — its numbers come from `DATA_<R>_TOTAL.watch`,
so switching A3/A5 swaps the formula to that report's 总量 data. It shows a single
看护公式 card (no title/subtitle) rendered as a **two-row grid** with two tall, emphasized boxes —
`社区总量用例` (blue, `.wf-result`) in the middle and `目标看护用例` (green, `.wf-watch`) on the right —
both stretched to span the two rows (`grid-row:1/3`, `.wf-tall`) with a **larger bold** number
(`font-size:1.5rem`, `font-weight:700`). The three term chips `社区日落用例` / `社区跳过用例` /
`黑名单跳过用例` (`.wf-muted`) are de-emphasized — smaller, grey text — while `收集用例` stays at the
default (dark number and label) as the source total. The top row carries `收集用例 − 社区日落用例 =` (`.watch-lhs`, column 1); the bottom row
carries `− 社区跳过用例 − 黑名单跳过用例 =` (`.watch-rhs`, column 3):

`收集用例 − 社区日落用例 = [社区总量用例]`
`[社区总量用例] − 社区跳过用例 − 黑名单跳过用例 = [目标看护用例]`

The `社区总量用例` box appears exactly once — the top row defines it, the bottom row consumes it.

Only the two result boxes are coloured: `社区总量用例` blue (`.wf-result`, `var(--gen-yes)`) and
`目标看护用例` green (`.wf-watch`, `#16a34a`). The three term chips (`社区日落用例` /
`社区跳过用例` / `黑名单跳过用例`) carry `.wf-muted` (neutral surface, smaller grey text) so they recede,
while `收集用例` uses the un-muted style (dark number and label) since it is the source total.
`.watch-flow` is a 4-column × 2-row grid (`justify-content:center`): `.watch-lhs` (col 1, row 1) →
社区总量用例 (col 2, rows 1–2) → `.watch-rhs` (col 3, row 2) → 目标看护用例 (col 4, rows 1–2);
`.watch-body` is a centered column (`flex-direction:column; align-items:center`), so the whole formula
centers as a group.

where `收集用例` (原社区总量用例, shown as a tooltip on the 收集用例 chip) = Σ
`实际运行数量` (= `all_testcases` rows), `社区总量用例` = `收集用例 − 社区日落用例`
(the shared middle box), `社区跳过用例` = `all_testcases` rows with 执行结果
`skipped`, `黑名单跳过用例` = `all_testcases` rows whose `不支持` (黑名单) column is `是`,
and `社区日落用例` = collected cases (`实际运行数量`) of files whose tracked priority is
`Should Not Do` (sunset files, from `--total-status`). **`社区跳过用例` and `黑名单跳过用例`
each exclude the Should Not Do files' rows** — those cases are already fully subtracted via
`社区日落用例`, so excluding them here keeps them from being double-subtracted. `build_total()`
computes `collected` / `community_skip` / `blocklist` / `snd` / `result`; the frontend derives the
`社区总量用例` intermediate as `collected − snd`.

**Should Not Do 口径 (总量).** For the Total (总量) data, every file whose tracked
`Priority == "Should Not Do"` (社区日落 / 废弃模块·历史遗留) is sunset, so its cases are excluded
from the 总量用例 scenario's *case-level* numbers: `build_two_tier()` is called with
`exclude_files = SND 文件路径集合`, dropping those files' cases from 概览 用例维度 / 各模块条 /
明细树 / `cases_total` (so `cases_total` == 社区总量用例, not 收集用例). The 测试文件 tab still
lists those files (Priority 列标注 `Should Not Do`, 保留其原始 `实际运行数量`) — they only stop
counting toward the case totals. `build_total()` keeps the top 看护公式 in terms of the raw
`收集用例`, subtracts the SND cases in full via `社区日落用例`, and (as above) excludes the SND
files' rows from `社区跳过用例`/`黑名单跳过用例`, so `目标看护用例 == passed + failed + timeout + error`
in exact agreement with the scenario's 用例执行结果分布.

(The 用例分布 公共/CPU/PU1 pie-chart card was removed, so `build_total()` no longer
emits the pre-collection `dist` split — only the 看护策略 `watch` numbers.)

Below 用例总览 there are **two cards**: the 用例总览 card (a normal rounded card, `margin-bottom` for
separation) and, below it, a single `.scenario-content` card that wraps both the 总量用例 / 解耦用例
views. The two 场景 tabs (总量用例 / 解耦用例) are shaped as regular trapezoids (正梯形 — narrow top,
wide bottom) and sit as a sibling row **sticking up from the top edge of the content card** (aligned to the
card's content, overlapping its top border by 1px via a negative bottom margin). Each tab is a
`<button class="scenario-tab">` containing an inline `<svg class="sc-tab-shape">` (a `<polyline
vector-effect="non-scaling-stroke">` traced `0,28 10,0 90,0 100,28` — i.e. the two slanted sides plus the
top edge) that is filled surface-card and stroked with `--border` at 1px; the polyline deliberately omits
the bottom segment, so the slanted + top edges get a uniform 1px outline exactly like the card's border,
while the open bottom lets the fill merge into the content card's surface — making the **selected** tab
continuous ("一体") with the content card's outline. An **inactive** tab instead draws a separate
`<line class="sc-tab-bottom">` along its bottom edge (hidden on `.active` via
`.scenario-tab.active .sc-tab-bottom{display:none}`), so the unselected trapezoid is fully closed with
the same 1px outline on all four sides. The two tabs overlap (a negative `margin-left` on `.scenario-tab + .scenario-tab` — negative flex
`gap` is unreliable, so margin is used instead) and the active tab is layered above the inactive one
(`z-index`); the active/inactive distinction is text
only (active = bold primary-coloured text, inactive = grey, hover lightens the fill). The tabs are the
scenario selector under the header A3/A5 report toggle — they switch the **scenario** and reload the page
while preserving the report: **总量用例** (the default when no `?scenario=` param is set; the transient
param is stripped after load so a refresh returns here) → `?scenario=TOTAL` (loads `cases_<report>_total.js`),
**解耦用例** → `?scenario=DECOUPLE` (loads `cases_<report>_decouple.js`). The 概览 / 用例详情 / 测试文件 views
are a single shared DOM that re-renders against
the active (report, scenario)'s `DATA` / `window.CASES` / `window.FILES`. The **用例解耦进度** card renders only for
the 解耦用例 scenario and is hidden for 总量用例 (`renderCollectProgress()` short-circuits
when `window.__DASH_SCENARIO__ === "TOTAL"`, because its target 社区总量用例 comes from the current report's
Total data itself and would exceed 100%).

## Input schema (the raw workbook)

Two layouts are supported, auto-detected by the sheet names present. Columns are
always located by header name (order-independent), with a positional fallback if
the header text differs.

### Current: dedicated `all_files` + `all_testcases` sheets

The module key is the **`sheet` column** on `all_files` — an explicit
`Core / Distributed / Graph / Math / Other / Quantization / Tensor / Utils`
assignment added in the 2026-09-10 export. When that column is present it is
authoritative and a `file → module` map is built from it. Older exports (before
2026-09-10) lack the column; the generator then falls back to the
`Classification → sheet` map built from the per-module case sheets (in which
`Classification` is the module, and `Tensor Operators`/`Tensor Types` are
canonicalized onto `Tensor`, see `_canonical_module`). The `Specialization`
column is the finer sub-division in both layouts.

- **`all_files`** — one row per test file (the file-level tier):
  | Column | Header | Meaning |
  |--------|--------|---------|
  | `sheet` | `sheet` | The module (sheet) the file belongs to — the authoritative module key. Forward-filled. Absent in pre-2026-09-10 exports |
  | `Classification` | `Classification` | Coarse module (`Core`/`Tensor Operators`/`Tensor Types`/…); used only as the module fallback when `sheet` is absent |
  | `Specialization` | `Specialization` | Fine sub-division (e.g. `Autograd`, `NN`, `CPU`, `Tools`) |
  | `File` | `File` | Test file path |
  | `num` | `num` or `实际运行数量` | Matched case count for that file (`0` → 未泛化/无用例文件); renamed `实际运行数量` in the 2026-09-10 export. Shown in the 测试文件 tab as 已泛化用例数 (black) |
  | `pub` | `预收集-公共用例` | Common (公共) pre-collected case count (shown in the 测试文件 tab); added in the 2026-09-17 export |
  | `cpu` | `CPU预收集` or `预收集-仅CPU` | CPU pre-collected case count (shown in the 测试文件 tab); renamed `预收集-仅CPU` in the 2026-09-17 export |
  | `npu` | `NPU预收集` or `预收集-仅NPU` | NPU pre-collected case count (shown in the 测试文件 tab); renamed `预收集-仅NPU` in the 2026-09-17 export |

- **`all_testcases`** — one row per executed case (the case-level tier), every
  cell populated:
  | Column | Header | Meaning |
  |--------|--------|---------|
  | `Classification` | `Classification` | Fine sub-division; folded onto its sheet for the module key |
  | `File` | `File` | Test file path |
  | `nodeid` | `nodeid` | Concrete test-case id |
  | `result` | `执行结果` | `passed` / `failed` / `skipped` / `not_executed` / `timeout` / `error` |

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
files_gen       = unique File values with num > 0 **and** Priority != "Should Not Do" (126)
files_snd       = all files whose Priority == "Should Not Do", regardless of num (174)
files_na        = files with num == 0, pre-collection > 0, Priority != "Should Not Do" (未泛化, 需泛化)
files_nocase    = files with num == 0, pre-collection == 0, Priority != "Should Not Do" (无用例文件)
files_gen_rate  = files_gen / (files_gen + files_na) × 100, 1 dec — 分母不含 无用例文件 / Should Not Do

cases_total     = rows in all_testcases + blacklisted cases       (179066)
cases.passed|failed|timeout|error = executed rows by 执行结果
cases.skipped   = executed skipped rows (running-skip included)   (17589)
cases.not_executed = executed not-run rows (未执行, non-watch)    (A5 only)
cases.blacklist_unsupported = blacklisted (disabled) cases        (16394)
blacklist_total = blacklisted rows total (all skip分类, incl. Running Skiped)
                  = cases.blacklist_unsupported + running-skip rows; the UI 含 blacklist
                  label shows cases.blacklist_unsupported, matching the 状态列/图例
watch_total     = passed + failed + timeout + error               (看护口径, 不含 skipped/blacklist/not_executed)
cases_pass_rate = cases.passed / watch_total × 100, 1 decimal     (看护通过率)

per module (grouped by sheet name):
  files        = unique File values in that module
  gen_files    = unique File values in that module with num > 0 **and** Priority != "Should Not Do"
  snd_files    = all files in that module with Priority == "Should Not Do", regardless of num
  na_files     = files in that module with num == 0 and pre-collection > 0 (未泛化, 需泛化)
  nocase_files = files in that module with num == 0 and pre-collection == 0 (无用例文件)
  gen_cases    = collected cases in that module (= sum of the 6 statuses)
  passed/failed/skipped/blacklist_unsupported/timeout/error = collected rows by status
```

> A file is generalized, needs generalization, is a no-case file, or is marked
> 无需泛化 — it is never counted in more than one file-level tier. `files_total` equals
> `files_gen + files_na + files_nocase + files_snd`, and per module
> `files == gen_files + na_files + nocase_files + snd_files`. The 已泛化/未泛化
> split is driven by executed `num`, but the two excluded tiers are classified
> **first**: every file whose `Priority == "Should Not Do"` is folded into its own
> tier (regardless of `num`), and every remaining file with `num == 0` and no
> pre-collection cases (公共+CPU+NPU all zero) is a 无用例文件 — neither of these
> two tiers counts into `files_gen_rate`, whose denominator is 已泛化 + 未泛化 only.
> So a Should Not Do file that is fully collected still reads as Should Not Do
> (not 已泛化), a no-case file reads as 无用例文件 (not 未泛化), and a file with
> *only* blacklisted cases (e.g. `test_jit.py`, 30 blacklist entries) still reads
> as 未泛化 at the file level while its blacklist cases appear in the case detail tree.

## How the two files are produced

The output is **two files** that sit side by side and work fully offline:

- **`index.html`** — the dashboard shell: all CSS/HTML/JS plus the small
  aggregate `DATA` objects, injected inline between one marker pair per
  (report, scenario) — `DATA_A3_TOTAL`, `DATA_A3_DECOUPLE`, `DATA_A5_TOTAL`,
  `DATA_A5_DECOUPLE` (see the datasets/scenario switch section above):

  ```js
  const DATA_A3_TOTAL    = /*__DATA_A3_TOTAL_BEGIN__*/    { ...aggregate... } /*__DATA_A3_TOTAL_END__*/;
  const DATA_A3_DECOUPLE = /*__DATA_A3_DECOUPLE_BEGIN__*/ { ...aggregate... } /*__DATA_A3_DECOUPLE_END__*/;
  const DATA_A5_TOTAL    = /*__DATA_A5_TOTAL_BEGIN__*/    { ...aggregate... } /*__DATA_A5_TOTAL_END__*/;
  const DATA_A5_DECOUPLE = /*__DATA_A5_DECOUPLE_BEGIN__*/ { ...aggregate... } /*__DATA_A5_DECOUPLE_END__*/;
  const DATA         = window["DATA_" + window.__DASH_REPORT__ + "_" + window.__DASH_SCENARIO__];
  const REPORT_TOTAL = window["DATA_" + window.__DASH_REPORT__ + "_TOTAL"];
  ```

- **`cases_a3_total.js`** / **`cases_a3_decouple.js`** / **`cases_a5_total.js`** / **`cases_a5_decouple.js`** — the bulky per-case detail tree, written as a single
  `window.CASES = { ... }` assignment and loaded by the shell via
  `<script src="cases_a3_total.js"></script>` (the file for the active report/scenario). Its shape is
  `module -> file -> [[nodeid_suffix, result], ...]`; the nodeid's file prefix is
  stripped and stored once per file, and the UI reconstructs the full nodeid as
  `file + "::" + suffix`. Executed cases are 2-element `[suffix, result]`;
  blacklisted cases are 4-element `[suffix, result, skip分类, skip原因]` so the
  用例详情 view can show the skip reason. A second assignment
  `window.FILES = [[module, file, gen, num, status, priority, assignee, cpu, npu, pub], ...]`
  (gen = 1/0, num = `实际运行数量` — the 已泛化用例数 shown in black;
  cpu/npu/pub = `CPU预收集`/`NPU预收集`/`预收集-公共用例`; status/priority/assignee come from the
  tracking sheet, `""` when the file is absent) backs the 测试文件 tab. This keeps
  `index.html` tiny no matter how many cases there are — hundreds of thousands of
  cases grow `cases_*.js`, not the HTML.

The script serializes `DATA` to JSON (a valid JS object literal) and replaces
everything between the markers; the `cases_*.js` files are regenerated from scratch each run.
Everything outside the `DATA` markers — CSS, HTML, canvas renderers, theme
toggle, table, detail-view code — is static and reused as-is.

## Interactive behaviors (static shell, preserved by regeneration)

These live in `index.html`'s rendering code (outside the `DATA` markers), so a
regeneration leaves them unchanged:

- **Click-to-drill.** The case donut (slices + legend items), the 各模块用例执行结果
  stacked bar, and the 模块详情汇总 table all jump to the 用例详情 tab via a global
  bridge `window.openCaseDetails(status, module)`:
  - case-donut slice / legend item → filter by that result. The donut only plots
    the 看护 (watch) statuses — `passed` / `failed` / `timeout_error` — and its
    slice angles, percentages and center 看护通过率 all use
    `watch_total = passed + failed + timeout + error` as the denominator.
    `skipped` and `blacklist_unsupported` are excluded from the pie and its
    percentages and rendered as muted side legend entries below a divider (still
    clickable to filter); the 超时/错误 slice maps to the combined `timeout_error`
    status and is drawn in the `--status-error` (Error) colour, matching the
    各模块用例执行结果 stacked bar.
  - stacked-bar status segment → filter by module + result; a module row's empty
    area → filter by module only. In this chart the `skipped`,
    `blacklist_unsupported` and `not_executed` segments are drawn lightened (muted)
    to downplay the non-看护 statuses (via a `lighten()` helper, legend dots
    matched). Each module row also draws a thin blue **收集目标** bar above the
    stacked bar — the module's should-collect total (`公共 + CPU + NPU` 预收集) — as a
    non-clickable reference on the same axis;
    the Should Not Do portion of that total is drawn in a lighter blue so the
    should-collect / Should Not Do split stays visible, and the axis max is
    `max(已收集最大值, 目标最大值)`. Both bars are equal height. To the right of the
    stacked bar each row shows a percentage — the module's collected cases
    (`caseTotal(sh)`) as a share of its 收集目标 **excluding the Should Not Do portion**
    (`target − snd`), i.e. `caseTotal / (target − snd) × 100%`, drawn in a muted
    secondary color, vertically centred on the bar's middle line with the font
    scaled to the bar height (`barH+3` px). The case and file donut slices are drawn
    contiguously (no `gapAngle` and no surface-coloured stroke between slices). Hovering the 收集目标
    bar shows a tooltip with its case count (`模块 · 收集目标 / N 用例`, plus the
    Should Not Do count when non-zero) but it stays non-clickable (cursor stays default,
    no drill-down).
  - module-summary table cell → the module name / 收集用例 cells filter by module
    only; a Passed/Failed/Skipped/Blacklist/Timeout/Error count filters by
    module + result;
    the 合计 row filters by the global (all-module) + result. Cells of zero-case
    modules are rendered plain (not clickable).
- **Should Not Do handling.** Files with `Priority=="Should Not Do"` (社区日落) don't count toward
  the should-collect target: their 公共/CPU/NPU 预收集 cases are shaded lighter blue in the module
  收集目标 bars, and `sndCaseTotals(files)` computes the per-module share client-side. This applies
  uniformly to both 总量用例 and 解耦用例 — the light-blue share covers *every* SND file's
  pre-collection, not just the ungeneralized ones. `sndCaseTotals` returns two metrics:
  `total`/`pub`/`cpu`/`npu` = pre-collection for the 收集目标 bars, and `num` = `实际运行数量`
  collected cases for the 收集测试用例 tile's "剔除 Should Not Do 的用例" figure.
- **Details filters.** The details toolbar has four filters — text search
  (module/file/nodeid), a module filter, a status filter (with a combined
  `timeout_error` option), and a skip-category filter (the distinct `skip分类`
  values, "全部 skip 类别" by default) — combined with AND. The module / status /
  skip-category filters are **multi-select** checkbox dropdowns (an empty selection
  means "all"; the dropdown is rendered by a `makeMultiSelect` helper that replaces
  the native `<select>`). `openCaseDetails` sets the relevant ones before switching
  views.
- **测试文件 tab.** Groups every test file by module (a collapsible module node
  whose children are that module's files, each showing path followed by fixed-width
  trailing columns in order 已泛化用例数 (num, black) / 公共收集 / CPU预收集 / NPU预收集 / assignee /
  status tag (Done/In Progress/Todo/Backlog) / priority tag
  (High/Medium/Low/Should Not Do) / 已泛化·未泛化·无用例文件·Should Not Do badge — every column
  always rendered so they line up vertically, empty when a value is missing; a
  `.tree-head` header row labels the columns). Its toolbar has a text search plus **multi-select**
  checkbox dropdowns for module, gen-status (全部/已泛化/未泛化/无用例文件/Should Not Do), status
  (全部状态/Done/In Progress/Todo/Backlog/未跟踪), priority (全部优先级/High/Medium/Low/
  Should Not Do/无优先级), and assignee (全部负责人/…/未分配, populated from the
  distinct assignees), all combined with AND. The `none` option in the status /
  priority / assignee filters means "empty field" (未跟踪 / 无优先级 / 未分配).
  The file-level charts drill down into it via `window.openFilesTab(filter)`:
  - 文件泛化率 donut slice / legend item → filter by gen status (已泛化 / 未泛化 / 无用例文件 / Should Not Do).
  - 各模块文件泛化情况 bar segment → filter by module + gen status (已泛化 / 未泛化 /
    无用例文件 / Should Not Do); a module row's empty (non-segment) area → filter by module only.
  - Clicking a generalized file row (its file label or 已泛化用例数) →
    `window.openCaseFile(module, file)`, which jumps to 用例详情 filtered to that
    file's cases; the 公共收集 / CPU预收集 / NPU预收集 columns are muted display-only
    and not clickable.
- **各模块文件泛化情况 bar sizing.** Each module row draws a horizontal stacked bar of
  已泛化 / 未泛化 / 无用例文件 / Should Not Do file counts (axis max = the largest module's
  `files`), capped at 14px tall (`barH = Math.min(14, …)`, 6px gaps); to its right the
  `已泛化/已泛化+未泛化` ratio label is drawn 6px past the bar's right edge, and `padR=80`
  reserves the room so it never overflows the canvas.
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

- `files_gen + files_na + files_nocase + files_snd == files_total`, and sum of per-sheet `files == files_total`
- sum of per-sheet `gen_files == files_gen`, per-sheet `snd_files == files_snd`,
  per-sheet `na_files == files_na`, per-sheet `nocase_files == files_nocase`,
  and per module `gen_files + na_files + nocase_files + snd_files == files`
- sum of per-sheet `gen_cases == cases_total`, and
  `passed+failed+skipped+blacklist_unsupported+timeout+error == cases_total`
- 看护通过率 `passed / watch_total` matches the pie center and the 模块详情汇总 通过率 column
  (watch_total = passed + failed + timeout + error, 不含 skipped/blacklist)

### Step 4: Sanity-check the HTML

Open `index.html` in a browser (works offline, no CDN; the `cases_*.js` files must be in the
same folder as `index.html`). Confirm:

- Overview top: the 用例总览 section (a single 看护策略 card from `DATA_<R>_TOTAL.watch`) fixed at the top
  of the page, followed by the 总量用例/解耦用例 场景 tabs (正梯形页签, sticking up from the top edge
  of the single `.scenario-content` card below, both tabs' outlines continuous with that card; global —
  reload-based scenario switch driving the 概览/用例详情/测试文件 tabs below, preserving the A3/A5 report);
  in the 解耦用例 scenario,
  the 用例解耦进度 card — a 解耦进度 bar (已收集 = 实际运行 + blacklist_total（含 Running Skiped，展示时并入
  skipped）; 目标 = 社区总量用例 = 收集用例 − 社区日落用例, from `DATA_<R>_TOTAL.watch`; 已收集/目标 counts and the
  percentage sit on one line above the bar) (the 用例目标 donut was removed). This card is **hidden** in the
  总量用例 scenario.
  The 4 summary tiles live inside this card as a single 1×4 row (the former 失败用例数
  tile was removed), in order:
  应解耦文件 (`files_total - files_snd - files_nocase`, sub 总量 `files_total` 扣除 Should Not Do `files_snd`、无用例文件 `files_nocase`) /
  已解耦文件 (`files_gen`, sub 泛化率) / 收集测试用例 (`cases_total - snd.num`, sub 含 blacklist
  `blacklist_unsupported`，其中剔除 Should Not Do 的 用例 `snd.num`) / 看护用例数 (通过 + 失败 + 错误/超时).
- Case-level: 用例执行结果分布 donut + 各模块用例执行结果 stacked bar (each row topped by a blue
  收集目标 reference bar) + 模块详情汇总 table
  (columns 模块 / 文件 / 已泛化 / 收集用例 / Passed / Failed / Skipped / Blacklist / Timeout / Error / 通过率,
  where 通过率 is 看护口径 — `passed / (passed + failed + timeout + error)`, excluding skipped/blacklist;
  sortable headers, no inline result-distribution bar)
- File-level: 文件泛化率 donut + 各模块文件泛化情况 stacked bar
- 用例详情 tab: drill-down 模块 → 文件 → 用例 (nodeid + 执行结果), with search / module / status /
  skip-category filters and chunked "加载更多" per file; blacklisted cases show their `skip分类`
  as a compact chip (the `skip原因` in its tooltip, keeping rows uncluttered)
- 测试文件 tab: files grouped by module (collapsible), each file showing path / 已泛化·未泛化·无用例文件·Should Not Do
  badge / case count, with search, module, and gen-status filters
- Clicking a case-donut slice/legend, a module-bar segment, a 模块详情汇总 table cell, or a
  file-level donut/bar segment jumps to 用例详情 / 测试文件 with the matching filter; hovering
  highlights without a border
- Light/dark toggle re-renders with correct status colors

## Notes & edge cases

- **Do not load the workbook in `read_only` mode.** This workbook reports broken
  dimension metadata (`max_row=1`) in read-only mode, which silently truncates
  iteration to the header row. The script loads in normal mode on purpose.
- **Known execution-status keys** are `passed` / `failed` / `skipped` /
  `not_executed` / `timeout` / `error` (plus the derived `blacklist_unsupported`).
  `not_executed` (未执行 — a case collected but not run, e.g. on the A5 hardware)
  is treated like `skipped`: excluded from the 看护通过率 denominator and drawn
  muted. Anything outside the known keys is folded into `error` with a `[warn]`
  line, so `gen_cases` always reconciles.
- **`top_specs` / `top_unsupported` are legacy.** Their charts were removed from
  the dashboard, so the generator no longer emits them. If those charts are
  re-added, derive them from the `Specialization` (grouped case counts) and
  `不支持原因` (grouped counts) columns and re-add them inside the markers.
- The dashboard is **two-tier by design**: 未泛化 / 无用例文件 entries are
  file-level only (no nodeid) and are excluded from all execution percentages.
