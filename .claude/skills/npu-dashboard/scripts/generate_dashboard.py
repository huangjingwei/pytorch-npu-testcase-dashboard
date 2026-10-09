#!/usr/bin/env python3
"""Generate the PyTorch NPU test-case dashboard from a raw results workbook.

Reads ``all_testcases.xlsx`` (one sheet per module, one row per file/case record)
and produces two files:

- ``index.html`` — the dashboard shell. Contains the small aggregate ``DATA``
  objects (one per report/scenario, injected between
  ``__DATA_<REPORT>_<SCENARIO>_BEGIN__`` / ``__DATA_<REPORT>_<SCENARIO>_END__``)
  and all rendering code, and loads the case details from a sibling file.
- ``cases_<report>_<scenario>.js`` — a single ``window.CASES = {...}`` assignment
  holding the compact per-case detail tree (module -> file ->
  [[nodeid_suffix, result], ...]), plus a flat
  ``window.FILES = [[module, file, gen, cases, status, priority, assignee], ...]``
  list for the 测试文件 tab. This is kept OUT of the HTML so the
  shell stays small even when
  there are hundreds of thousands of cases.

Both files sit side by side and work fully offline (a ``<script src>`` tag, not a
blocked ``fetch``). The nodeid's file prefix is stripped and stored once per file;
the UI reconstructs the full nodeid as ``file + "::" + suffix``.

Usage:
    python3 generate_dashboard.py                     # default paths
    python3 generate_dashboard.py --input new.xlsx --output index.html
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl is required: pip3 install openpyxl")

# Sentinel used in the raw data for a row that has no matched nodeid, i.e. a
# file-level (未泛化) record rather than a concrete test case.
UNMATCHED_NODEID = "(未匹配)"

# Execution-status keys the dashboard knows about, in display order. ``not_executed``
# (未执行 — collected but not run on this hardware) is a non-watch status like
# ``skipped``: it is excluded from the 看护通过率 denominator.
STATUS_KEYS = ["passed", "failed", "skipped", "not_executed", "timeout", "error"]

# Header names (verbatim) the script looks for, with 0-based positional fallback
# when the header row does not match (robust to column reordering).
COLUMN_SPEC = {
    "sheet":  {"names": ["sheet"], "fallback": None},
    "file":   {"names": ["File"],   "fallback": 2},
    "nodeid": {"names": ["nodeid"], "fallback": 3},
    "result": {"names": ["执行结果"], "fallback": 4},
    "class":  {"names": ["Classification"], "fallback": 0},
    "num":    {"names": ["num", "实际运行数量"], "fallback": 4},
    "skip_cls":    {"names": ["skip分类"], "fallback": 5},
    "skip_reason": {"names": ["skip原因"], "fallback": 6},
    "unsupported": {"names": ["不支持"], "fallback": None},
}

def data_markers(report, scenario):
    """Injection markers for a (report, scenario) pair — ``__DATA_<REPORT>_<SCENARIO>_...``
    — so all four datasets (A3/A5 × 总量/解耦) coexist in one HTML file."""
    key = f"{report}_{scenario}"
    return f"/*__DATA_{key}_BEGIN__*/", f"/*__DATA_{key}_END__*/"


def cases_filename(report, scenario):
    """Case-detail JS filename for a (report, scenario) pair —
    ``cases_<report>_<scenario>.js`` (report lower-cased)."""
    return f"cases_{report.lower()}_{scenario.lower()}.js"


def _find_column(headers, spec):
    for i, h in enumerate(headers):
        if h is not None and str(h).strip() in spec["names"]:
            return i
    return spec["fallback"]


def _precollect_names(dataset):
    """Pre-collection column names for a dataset. The 2026-09-23 export split the
    pre-collection counts into per-dataset columns (``A3-公共用例`` / ``A3-仅CPU`` /
    ``A3-仅NPU`` and the A5 equivalents); older exports carried a single set
    (``预收集-公共用例`` / ``预收集-仅CPU`` / ``预收集-仅NPU``).

    The 2026-09-29 export renamed the A3 columns to ``A3全量-公共用例`` /
    ``A3全量-仅CPU`` / ``A3全量-仅NPU`` (and dropped the A5 column set), so both
    ``<KEY>-…`` and ``<KEY>全量-…`` spellings are matched.

    The ``TOTAL`` (总量) workbook carries the *same* ``A3…`` pre-collection
    columns as the A3 report (there is no ``TOTAL…`` set), so ``TOTAL`` resolves
    to the A3 column names rather than a non-existent ``TOTAL…`` prefix."""
    key = "A3" if dataset == "TOTAL" else dataset
    p = key + "-"
    pfull = key + "全量-"
    return {
        "pub": [pfull + "公共用例", p + "公共用例", "预收集-公共用例"],
        "cpu": [pfull + "仅CPU", p + "仅CPU", "CPU预收集", "预收集-仅CPU"],
        "npu": [pfull + "仅NPU", p + "仅NPU", "NPU预收集", "预收集-仅NPU"],
    }


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    return v if v is None else str(v).strip()


def _to_int(v):
    """Coerce a possibly-None/numeric cell to int, defaulting to 0."""
    try:
        return int(v) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def is_matched(nodeid):
    """True when a row represents a concrete, generalized test case."""
    return bool(nodeid) and nodeid != UNMATCHED_NODEID


def _canonical_module(name):
    """Fold the Tensor sub-sheets back onto ``Tensor`` (the legacy module key).
    The current schema splits Tensor into separate ``Tensor Operators`` and
    ``Tensor Types`` sheets; both are reported as a single ``Tensor`` module."""
    if name in ("Tensor Operators", "Tensor Types"):
        return "Tensor"
    return name


def build(path, blacklist_path=None, dataset="A3", unsupported_to_blacklist=False,
          exclude_files=None):
    """One pass over the workbook -> (aggregate DATA dict, per-case detail tree,
    flat file list).

    ``exclude_files`` is an optional set of file paths whose *cases* are dropped
    from the case-level aggregation (case totals, per-module counts and the detail
    tree). The 总量 (TOTAL) build passes the ``Should Not Do`` file set here so the
    scenario's case statistics match the top 「用例总览」 社区总量用例 figure."""
    # NOTE: not read_only — this workbook reports broken dimension metadata in
    # read-only mode (max_row=1), which would silently truncate iteration.
    wb = openpyxl.load_workbook(path, data_only=True)
    sheet_names = {ws.title for ws in wb.worksheets}
    if "all_files" in sheet_names and "all_testcases" in sheet_names:
        return build_two_tier(wb, blacklist_path, dataset, unsupported_to_blacklist,
                              exclude_files)
    return build_legacy(wb)


def _is_blacklist_sheet(title):
    """True for a worksheet holding blacklisted/disabled cases — the current
    in-workbook ``黑名单跳过`` sheet or the legacy ``all_blacklist`` sheet."""
    t = str(title).strip()
    return "黑名单" in t or "blacklist" in t.lower()


def _blacklist_entries(header, rows, file_module):
    """Yield ``(module, file, nodeid_suffix, result, skip_cls, skip_reason)``
    tuples from a blacklist sheet's header + rows.

    ``skip分类`` containing ``running`` (case-insensitive) — the "Running Skiped"
    entries — folds into ``skipped``; every other blacklisted case becomes the new
    ``blacklist_unsupported`` status. Both ``Classification`` and ``File`` are
    forward-filled (merged-cell convention). The module is resolved from the
    ``file -> module`` map (``file_module``), falling back to the forward-filled
    ``Classification`` when the file is unknown."""
    col_cls = _find_column(header, COLUMN_SPEC["class"])
    col_file = _find_column(header, COLUMN_SPEC["file"])
    col_nodeid = _find_column(header, COLUMN_SPEC["nodeid"])
    col_skip_cls = _find_column(header, COLUMN_SPEC["skip_cls"])
    col_skip_reason = _find_column(header, COLUMN_SPEC["skip_reason"])

    cur_cls = None
    cur_file = None
    for row in rows:
        cls = _cell(row, col_cls)
        if cls:
            cur_cls = cls
        f = _cell(row, col_file)
        if f:
            cur_file = f
        if not cur_file:
            continue
        nodeid = _cell(row, col_nodeid)
        if not nodeid:
            continue
        skip_cls = _cell(row, col_skip_cls) or ""
        skip_reason = _cell(row, col_skip_reason) or ""
        module = (file_module or {}).get(cur_file) or cur_cls or "Other"
        result = "skipped" if "running" in skip_cls.lower() else "blacklist_unsupported"
        suffix = nodeid[len(cur_file) + 2:] if nodeid.startswith(cur_file + "::") else nodeid
        yield (module, cur_file, suffix, result, skip_cls, skip_reason)


def load_blacklist(path, file_module):
    """Read a legacy *separate* blacklist workbook and return its ``(module, file,
    nodeid_suffix, result, skip_cls, skip_reason)`` tuples. Only used when the
    input workbook has no in-sheet blacklist (``黑名单跳过``)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = None
    for sh in wb.worksheets:
        if _is_blacklist_sheet(sh.title):
            ws = sh
            break
    if ws is None:
        return []
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        return []
    return list(_blacklist_entries(header, rows, file_module))


# Tracked generalization status, one sheet per module. Each row carries a
# ``Status`` cell like "🟢 Done" / "🔵 Todo" / "🟡 In Progress" / "⚪ Backlog";
# we keep just the text label (the coloured circle is a display emoji).
def _normalize_status(raw):
    if not raw:
        return ""
    raw = raw.strip()
    if raw and not raw[0].isascii():
        raw = raw[1:].strip()
    return raw


def load_status(path):
    """Read the tracking workbook and return a ``file path -> (status, priority,
    assignee)`` map.

    Only the module sheets are read (``README`` sheets are skipped). The ``File``
    column is located by header prefix (the header may be ``File`` or ``File(N)``
    where N is the per-module file count); status is read from ``Status`` (or the
    newer ``社区status``), priority from ``Priority``, and assignee from
    ``Assignee`` (or the newer ``author``). Each non-empty file row contributes
    its status, priority and assignee. Matching is by exact file path, so a
    dashboard file absent from this sheet simply has no tracking data."""
    wb = openpyxl.load_workbook(path, data_only=True)
    track = {}
    for ws in wb.worksheets:
        if ws.title.lower().startswith("readme"):
            continue
        header = next(ws.iter_rows(values_only=True), None)
        if header is None:
            continue
        col_file = col_status = col_priority = col_assignee = None
        for i, h in enumerate(header):
            if h is None:
                continue
            hs = str(h).strip()
            if col_file is None and hs.startswith("File"):
                col_file = i
            elif col_status is None and hs in ("Status", "社区status"):
                col_status = i
            elif col_priority is None and hs == "Priority":
                col_priority = i
            elif col_assignee is None and hs in ("Assignee", "author"):
                col_assignee = i
        if col_file is None or col_status is None:
            continue
        for row in ws.iter_rows(min_row=2, values_only=True):
            f = _cell(row, col_file)
            if not f:
                continue
            track[f] = (_normalize_status(_cell(row, col_status)),
                        _cell(row, col_priority) or "",
                        _cell(row, col_assignee) or "")
    return track


def load_precollect(path):
    """Read a workbook's ``all_files`` sheet and return ``{file: (cpu, npu, pub)}``
    — the per-file pre-collection counts (收集目标). Used to align the 解耦用例
    scenario's target with the Total (总量) workbook when the decouple export's own
    pre-collection columns come back empty."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["all_files"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    pre = _precollect_names("TOTAL")
    col_file = _find_column(header, COLUMN_SPEC["file"])
    col_pub = _find_column(header, {"names": pre["pub"], "fallback": None})
    col_cpu = _find_column(header, {"names": pre["cpu"], "fallback": None})
    col_npu = _find_column(header, {"names": pre["npu"], "fallback": None})
    out = {}
    for row in rows:
        f = _cell(row, col_file)
        if not f:
            continue
        out[f] = (_to_int(_cell(row, col_cpu)),
                  _to_int(_cell(row, col_npu)),
                  _to_int(_cell(row, col_pub)))
    wb.close()
    return out


def build_total(path, status_path=None):
    """Read the *Total* (总量) workbook and compute the top-of-overview
    「用例总览」 看护策略 numbers, rendered by the frontend as a two-row grid —
    ``收集用例 − 社区日落用例 = 社区总量用例``
    (top row) and ``社区总量用例 − 社区跳过用例 − 黑名单跳过用例 =
    目标看护用例`` (bottom row) — with ``社区总量用例`` and ``目标看护用例``
    as tall boxes spanning both rows, their numbers larger and bold
    (``.wf-chip.wf-tall .wf-n`` → 1.5rem/700), while the three term chips
    (``社区日落用例`` / ``社区跳过用例`` / ``黑名单跳过用例``) are
    de-emphasized as smaller grey ``.wf-muted`` chips (``收集用例`` stays at
    the default dark style — number and label — as the source total).

    ``收集用例`` (原社区总量用例) is the sum of ``实际运行数量`` (== ``all_testcases``
    rows); the frontend derives ``社区总量用例`` as ``收集用例 − 社区日落用例``.
    ``社区跳过用例`` counts ``all_testcases`` rows whose 执行结果 is ``skipped``;
    ``黑名单跳过用例`` counts ``all_testcases`` rows whose ``不支持`` (黑名单) column is
    set (``是``) — the NPU-unsupported cases; ``社区日落用例`` is the
    collected total (``实际运行数量``) of files whose tracked priority is
    ``Should Not Do`` (sunset files; 0 unless a tracking workbook is supplied).

    ``社区日落用例`` files' rows are *also* excluded from ``社区跳过用例`` and
    ``黑名单跳过用例``, so the resulting ``目标看护用例`` reconciles exactly with
    the scenario's SND-excluded 用例执行结果分布 (``passed + failed + error``);
    otherwise the SND cases that are themselves skipped/unsupported would be
    subtracted twice (once via these chips, once via ``社区日落用例``)."""
    wb = openpyxl.load_workbook(path, data_only=True)

    ws = wb["all_files"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    col_file = _find_column(header, COLUMN_SPEC["file"])
    col_num = _find_column(header, COLUMN_SPEC["num"])

    collected = 0
    file_num = {}
    for row in rows:
        f = _cell(row, col_file)
        if not f:
            continue
        num = _to_int(_cell(row, col_num))
        collected += num
        file_num[f] = num

    # 社区日落用例 = collected cases (实际运行数量) of files whose tracked priority
    # is "Should Not Do" (sunset files). Every Should Not Do file is sunset, so its
    # actually-collected cases are subtracted from the watch total. Its file paths
    # are kept so the 社区跳过/黑名单 loops below can skip their rows — otherwise
    # the SND cases that are themselves skipped/unsupported would be double-subtracted.
    snd = 0
    snd_files = set()
    if status_path:
        track = load_status(status_path)
        for f, num in file_num.items():
            if track.get(f, ("", "", ""))[1] == "Should Not Do":
                snd_files.add(f)
                snd += num

    # 社区跳过用例 = skipped rows in all_testcases;
    # 黑名单跳过用例 = rows in all_testcases whose 不支持(黑名单) is set (unsupported).
    # Should Not Do 文件（社区日落用例）的用例已在上方按 实际运行数量 全额从 snd
    # 扣除，这里跳过其行，使「目标看护用例」与场景内剔除 SND 后的用例执行结果分布
    # （passed + failed + error）严格对齐。
    community_skip = 0
    blocklist = 0
    sheet_names = {ws.title for ws in wb.worksheets}
    if "all_testcases" in sheet_names:
        ws = wb["all_testcases"]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        col_file = _find_column(header, COLUMN_SPEC["file"])
        col_result = _find_column(header, COLUMN_SPEC["result"])
        col_unsupported = _find_column(header, COLUMN_SPEC["unsupported"])
        for row in rows:
            if snd_files and _cell(row, col_file) in snd_files:
                continue
            if (_cell(row, col_result) or "").lower() == "skipped":
                community_skip += 1
            if _cell(row, col_unsupported):
                blocklist += 1

    watch = collected - community_skip - blocklist - snd
    return {
        "watch": {
            "collected": collected,
            "community_skip": community_skip,
            "blocklist": blocklist,
            "snd": snd,
            "result": watch,
        },
    }


def build_two_tier(wb, blacklist_path=None, dataset="A3", unsupported_to_blacklist=False,
                   exclude_files=None):
    """New schema: a dedicated ``all_files`` sheet (one row per file, ``num`` /
    ``实际运行数量`` = matched case count) plus an ``all_testcases`` sheet (one row
    per case).

    The module key is the ``sheet`` column on ``all_files`` (present in the
    current export: Core / Distributed / … / Utils / Tensor). When that column is
    absent (older exports) we fall back to the ``Classification -> sheet`` map
    built from the per-module case sheets.

    ``unsupported_to_blacklist`` reclassifies ``all_testcases`` rows whose
    ``不支持`` (黑名单) column is set as ``blacklist_unsupported`` instead of their
    raw result. The 全量 (TOTAL) report encodes NPU-unsupported cases inline this
    way (its ``黑名单跳过`` sheet is empty), so only the TOTAL build sets this flag;
    the community-decoupling (A3/A5) reports keep their separate blacklist sheet
    handling untouched.

    ``exclude_files`` (a set of file paths) drops those files' cases from the
    case-level aggregation entirely — used by the TOTAL build to exclude the
    ``Should Not Do`` files' cases (社区日落用例) so the scenario's case counts
    match the top 「用例总览」 社区总量用例 figure."""
    case_totals = Counter()
    sheets = {}
    detail = {}
    file_list = []  # [[module, file, gen(0/1), num, cpu, npu, pub], ...] for the 测试文件 tab

    # Map each Classification value to its owning sheet name (module). The
    # per-module case sheets are the source of truth here; Classification is
    # forward-filled per file within each sheet.
    cls_to_sheet = {}
    for ws in wb.worksheets:
        if ws.title in ("all_files", "all_testcases") or _is_blacklist_sheet(ws.title):
            continue
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            continue
        col_cls = _find_column(header, COLUMN_SPEC["class"])
        cur = None
        for row in rows:
            cls = _cell(row, col_cls)
            if cls:
                cur = cls
            if cur:
                cls_to_sheet[cur] = _canonical_module(ws.title)

    # ---- File-level tier: all_files (Classification forward-filled) ----
    # The current export carries an explicit ``sheet`` column naming the module a
    # file belongs to (Core / Distributed / … / Utils / Tensor); it is
    # authoritative when present. Older exports omit it, so we fall back to the
    # ``Classification -> sheet`` map built from the per-module case sheets.
    ws = wb["all_files"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    col_sheet = _find_column(header, COLUMN_SPEC["sheet"])
    col_cls = _find_column(header, COLUMN_SPEC["class"])
    col_file = _find_column(header, COLUMN_SPEC["file"])
    col_num = _find_column(header, COLUMN_SPEC["num"])
    pre = _precollect_names(dataset)
    col_pub = _find_column(header, {"names": pre["pub"], "fallback": None})
    col_cpu = _find_column(header, {"names": pre["cpu"], "fallback": None})
    col_npu = _find_column(header, {"names": pre["npu"], "fallback": None})

    file_module = {}
    file_gen = {}
    file_num = {}
    file_pub = {}
    file_cpu = {}
    file_npu = {}
    module_files = defaultdict(set)
    module_gen_files = defaultdict(set)

    cur_sheet = None
    cur_cls = None
    for row in rows:
        if col_sheet is not None:
            s = _cell(row, col_sheet)
            if s:
                cur_sheet = s
        cls = _cell(row, col_cls)
        if cls:
            cur_cls = cls
        f = _cell(row, col_file)
        if not f:
            continue
        if cur_sheet:
            module = _canonical_module(cur_sheet)
        else:
            module = cls_to_sheet.get(cur_cls, cur_cls) or "Other"
        gen = _to_int(_cell(row, col_num)) > 0
        file_module[f] = module
        file_gen[f] = gen
        file_num[f] = _to_int(_cell(row, col_num))
        file_pub[f] = _to_int(_cell(row, col_pub))
        file_cpu[f] = _to_int(_cell(row, col_cpu))
        file_npu[f] = _to_int(_cell(row, col_npu))
        module_files[module].add(f)
        if gen:
            module_gen_files[module].add(f)

    all_files = set(file_module)
    all_gen_files = {f for f, g in file_gen.items() if g}

    # ---- Case-level tier: all_testcases (every row fully populated) ----
    ws = wb["all_testcases"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    col_cls = _find_column(header, COLUMN_SPEC["class"])
    col_file = _find_column(header, COLUMN_SPEC["file"])
    col_nodeid = _find_column(header, COLUMN_SPEC["nodeid"])
    col_result = _find_column(header, COLUMN_SPEC["result"])
    col_unsupported = _find_column(header, COLUMN_SPEC["unsupported"])

    module_cases = defaultdict(Counter)
    module_detail = defaultdict(dict)

    for row in rows:
        cls = _cell(row, col_cls)
        f = _cell(row, col_file)
        nodeid = _cell(row, col_nodeid)
        result = _cell(row, col_result)
        if not nodeid:
            continue
        if exclude_files and f in exclude_files:
            # 总量报告：剔除 Should Not Do 文件对应用例（社区日落用例），
            # 使场景内各用例数量统计与顶部「社区总量用例」口径一致。
            continue
        # The file -> module map (from all_files) is authoritative; fall back to
        # the Classification -> sheet map for files it does not know.
        module = (file_module.get(f)
                  or (cls_to_sheet.get(cls, cls) if cls else "Other"))
        result = (result or "error").lower()
        if unsupported_to_blacklist and _cell(row, col_unsupported):
            # 全量报告：NPU 不支持（黑名单）用例内联在 all_testcases，标记为
            # blacklist_unsupported，与社区解耦报告的独立黑名单 sheet 口径一致。
            result = "blacklist_unsupported"
        elif result not in STATUS_KEYS:
            sys.stderr.write(f"[warn] all_testcases: unknown result "
                             f"{result!r} -> error\n")
            result = "error"
        case_totals[result] += 1
        module_cases[module][result] += 1

        suffix = nodeid[len(f) + 2:] if f and nodeid.startswith(f + "::") else nodeid
        module_detail[module].setdefault(f, []).append([suffix, result])

    # ---- Blacklist tier (optional): fold blacklisted cases into the totals ----
    # Running-skip entries land in "skipped"; everything else becomes the new
    # "blacklist_unsupported" status. Detail entries carry [suffix, result,
    # skip_cls, skip_reason] so the UI can show the skip分类 / skip原因.
    blacklist_total = 0
    # Distinct skip分类 values grouped by the result status they fold into, in
    # first-appearance order. The UI offers only the categories relevant to the
    # currently selected status — "Running Skiped" belongs to `skipped` while
    # "device not supported" / "cann not supported" belong to
    # `blacklist_unsupported` — rather than a flat list of every category.
    skip_cls_by_status = {"skipped": [], "blacklist_unsupported": []}
    skip_cls_seen = {"skipped": set(), "blacklist_unsupported": set()}
    # Blacklist source: the current schema carries it as an in-workbook
    # ``黑名单跳过`` sheet; the legacy schema keeps it in a separate workbook.
    blacklist_entries = []
    for ws in wb.worksheets:
        if _is_blacklist_sheet(ws.title):
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if header is not None:
                blacklist_entries = list(_blacklist_entries(header, rows, file_module))
            break
    if not blacklist_entries and blacklist_path:
        blacklist_entries = load_blacklist(blacklist_path, file_module)
    for module, f, suffix, result, skip_cls, skip_reason in blacklist_entries:
        if exclude_files and f in exclude_files:
            continue
        blacklist_total += 1
        if skip_cls and skip_cls not in skip_cls_seen[result]:
            skip_cls_seen[result].add(skip_cls)
            skip_cls_by_status[result].append(skip_cls)
        case_totals[result] += 1
        module_cases[module][result] += 1
        module_detail[module].setdefault(f, []).append([suffix, result, skip_cls, skip_reason])

    # ---- Combine into per-module sheets ----
    for module in sorted(set(module_files) | set(module_cases)):
        files = module_files[module]
        gen_files = module_gen_files[module]
        st = module_cases[module]
        sheets[module] = {
            "files": len(files),
            "gen_files": len(gen_files),
            "na_files": len(files) - len(gen_files),
            "gen_cases": sum(st.values()),
            "passed": st["passed"],
            "failed": st["failed"],
            "skipped": st["skipped"],
            "not_executed": st["not_executed"],
            "timeout": st["timeout"],
            "error": st["error"],
            "blacklist_unsupported": st["blacklist_unsupported"],
        }
        if module_detail.get(module):
            detail[module] = module_detail[module]
        for f in sorted(files):
            file_list.append([module, f, 1 if f in gen_files else 0,
                              file_num.get(f, 0),
                              file_cpu.get(f, 0),
                              file_npu.get(f, 0),
                              file_pub.get(f, 0)])

    files_total = len(all_files)
    files_gen = len(all_gen_files)
    files_na = files_total - files_gen
    cases_total = sum(case_totals.values())
    watch_total = (case_totals["passed"] + case_totals["failed"]
                   + case_totals["timeout"] + case_totals["error"])

    data = {
        "files_total": files_total,
        "files_gen": files_gen,
        "files_na": files_na,
        "files_gen_rate": round(files_gen / files_total * 100, 1) if files_total else 0.0,
        "cases_total": cases_total,
        "cases": {
            "passed": case_totals["passed"],
            "failed": case_totals["failed"],
            "skipped": case_totals["skipped"],
            "not_executed": case_totals["not_executed"],
            "timeout": case_totals["timeout"],
            "error": case_totals["error"],
            "blacklist_unsupported": case_totals["blacklist_unsupported"],
        },
        "blacklist_total": blacklist_total,
        "skip_categories": skip_cls_by_status,
        "cases_pass_rate": round(case_totals["passed"] / watch_total * 100, 1) if watch_total else 0.0,
        "sheets": sheets,
    }
    return data, detail, file_list


def build_legacy(wb):
    """Legacy schema: one sheet per module (sheet name = module key); a nodeid of
    ``(未匹配)`` or empty marks a file-level (未泛化) record."""
    all_files = set()
    all_gen_files = set()
    case_totals = Counter()
    sheets = {}
    detail = {}
    file_list = []  # [[module, file, gen(0/1), num, cpu, npu, pub], ...] for the 测试文件 tab

    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            continue  # empty sheet

        col_file = _find_column(header, COLUMN_SPEC["file"])
        col_nodeid = _find_column(header, COLUMN_SPEC["nodeid"])
        col_result = _find_column(header, COLUMN_SPEC["result"])

        files = set()
        gen_files = set()
        status = Counter()
        file_cases = Counter()
        module_detail = {}

        cur_file = None
        for row in rows:
            f = _cell(row, col_file)
            nodeid = _cell(row, col_nodeid)
            result = _cell(row, col_result)

            # The File column is only populated on the first row of each file's
            # case group; continuation rows leave it blank. Forward-fill it so
            # every case row still resolves to its owning file.
            if f:
                cur_file = f
            if not cur_file:
                continue  # blank leading row with no file context yet
            f = cur_file

            files.add(f)
            all_files.add(f)

            if is_matched(nodeid):
                gen_files.add(f)
                all_gen_files.add(f)
                result = (result or "error").lower()
                if result not in STATUS_KEYS:
                    # Unknown statuses are folded into "error" so totals reconcile.
                    sys.stderr.write(f"[warn] {ws.title}: unknown result "
                                     f"{result!r} -> error\n")
                    result = "error"
                status[result] += 1
                case_totals[result] += 1
                file_cases[f] += 1

                # Detail tree: strip the file prefix from the nodeid (stored once
                # per file); the UI reconstructs the full nodeid as file+"::"+suffix.
                suffix = nodeid[len(f) + 2:] if nodeid.startswith(f + "::") else nodeid
                module_detail.setdefault(f, []).append([suffix, result])

        if not files:
            continue  # sheet with a header but no data rows

        sheets[ws.title] = {
            "files": len(files),
            "gen_files": len(gen_files),
            "gen_cases": sum(status.values()),
            "passed": status["passed"],
            "failed": status["failed"],
            "skipped": status["skipped"],
            "not_executed": status["not_executed"],
            "blacklist_unsupported": 0,
            "timeout": status["timeout"],
            "error": status["error"],
            "na_files": len(files) - len(gen_files),
        }
        if module_detail:
            detail[ws.title] = module_detail
        for f in sorted(files):
            file_list.append([ws.title, f, 1 if f in gen_files else 0,
                              file_cases.get(f, 0), 0, 0, 0])

    files_total = len(all_files)
    files_gen = len(all_gen_files)
    files_na = files_total - files_gen
    cases_total = sum(case_totals.values())
    watch_total = (case_totals["passed"] + case_totals["failed"]
                   + case_totals["timeout"] + case_totals["error"])

    data = {
        "files_total": files_total,
        "files_gen": files_gen,
        "files_na": files_na,
        "files_gen_rate": round(files_gen / files_total * 100, 1) if files_total else 0.0,
        "cases_total": cases_total,
        "cases": {
            "passed": case_totals["passed"],
            "failed": case_totals["failed"],
            "skipped": case_totals["skipped"],
            "not_executed": case_totals["not_executed"],
            "blacklist_unsupported": 0,
            "timeout": case_totals["timeout"],
            "error": case_totals["error"],
        },
        "blacklist_total": 0,
        "skip_categories": {"skipped": [], "blacklist_unsupported": []},
        "cases_pass_rate": round(case_totals["passed"] / watch_total * 100, 1) if watch_total else 0.0,
        "sheets": sheets,
    }
    return data, detail, file_list


def inject(html, begin, end, body):
    """Replace everything between `begin` and `end` (inclusive) with `begin+body+end`."""
    if begin not in html:
        sys.exit(f"error: marker {begin} not found in template")
    start = html.index(begin)
    stop = html.index(end, start)
    if stop < start:
        sys.exit(f"error: marker {end} not found after {begin}")
    return html[:start] + begin + body + end + html[stop + len(end):]


def attach_status_and_fold(data, file_list, status_path, total_precollect=None):
    """Attach tracked status/priority/assignee (5th/6th/7th elements) and the
    pre-collection counts (8th/9th/10th) to each file list entry, then fold
    "Should Not Do" (无需泛化) files out of the 未泛化 bucket so the file-level
    charts can show them as a distinct third category.

    ``total_precollect`` (optional ``{file: (cpu, npu, pub)}`` from the Total
    workbook) aligns the scenario's pre-collection (收集目标) and the 未泛化 /
    无用例文件 split with the Total data: files present in the Total use its
    pre-collection, and files absent from the Total are treated as 未泛化 (presumed
    to have a target the older Total snapshot predates)."""
    track = load_status(status_path) if status_path else {}
    file_list = [[m, f, g, num] + list(track.get(f, ("", "", ""))) + [cpu, npu, pub]
                 for m, f, g, num, cpu, npu, pub in file_list]
    total_files = set(total_precollect) if total_precollect else None
    if total_files is not None:
        for e in file_list:
            if e[1] in total_files:
                e[7], e[8], e[9] = total_precollect[e[1]]
    snd_by_module = defaultdict(int)
    gen_by_module = defaultdict(int)      # 已泛化 = gen 且非 Should Not Do（Should Not Do 单独一类）
    na_by_module = defaultdict(int)       # 未泛化 = 有预收集用例但尚未收集（需泛化）
    nocase_by_module = defaultdict(int)   # 无用例文件 = 既无收集也无预收集用例（不计入泛化率）
    module_target = defaultdict(int)      # 收集目标 = 各模块 公共+CPU+NPU 预收集之和（总量用例口径）
    module_snd_target = defaultdict(int)  # 其中 Should Not Do 文件的预收集份额（淡蓝）
    for m, f, g, num, status, priority, assignee, cpu, npu, pub in file_list:
        pre = (cpu or 0) + (npu or 0) + (pub or 0)
        module_target[m] += pre
        if priority == "Should Not Do":
            module_snd_target[m] += pre
            snd_by_module[m] += 1          # 所有 Should Not Do 文件，无论是否已泛化
        elif g == 1:
            gen_by_module[m] += 1          # 已泛化 = gen 且非 Should Not Do
        elif total_files is not None and f not in total_files:
            na_by_module[m] += 1           # 不在 Total 内 → 视为未泛化（对齐总量口径）
        elif pre > 0:
            na_by_module[m] += 1           # 未泛化 = 有预收集用例但尚未收集
        else:
            nocase_by_module[m] += 1       # 无用例文件 = 无收集也无预收集用例
    data["files_snd"] = sum(snd_by_module.values())
    data["files_gen"] = sum(gen_by_module.values())
    data["files_na"] = sum(na_by_module.values())
    data["files_nocase"] = sum(nocase_by_module.values())
    # 泛化率只统计 已泛化 + 未泛化（不含 无用例文件 / Should Not Do）
    gen_na = data["files_gen"] + data["files_na"]
    data["files_gen_rate"] = round(data["files_gen"] / gen_na * 100, 1) if gen_na else 0.0
    data["module_target"] = dict(module_target)
    data["module_snd_target"] = dict(module_snd_target)
    for name, sh in data["sheets"].items():
        sh["snd_files"] = snd_by_module.get(name, 0)
        sh["gen_files"] = gen_by_module.get(name, 0)
        sh["na_files"] = na_by_module.get(name, 0)
        sh["nocase_files"] = nocase_by_module.get(name, 0)
    return data, file_list


def main(argv=None):
    p = argparse.ArgumentParser(description="Regenerate the NPU test dashboard.")
    p.add_argument("--input", "-i", default="all_testcases.xlsx",
                   help="Input workbook (default: all_testcases.xlsx)")
    p.add_argument("--output", "-o", default="index.html",
                   help="Output/template HTML (default: index.html)")
    p.add_argument("--cases-out", "-c", default=None,
                   help="Where to write the case-detail JS (default: <output dir>/cases_<report>_decouple.js)")
    p.add_argument("--blacklist", "-b", default=None,
                   help="Blacklist workbook (default: blacklist_testcases.xlsx next to --input, if present)")
    p.add_argument("--status", "-s", default=None,
                   help="Tracking workbook (default: status_tracking.xlsx next to --input, if present); attaches Status/Priority/Assignee to each file")
    p.add_argument("--json-out", help="Optional: also write DATA to a .json file")
    p.add_argument("--dataset", "-d", default="A3",
                   help="Report key (A3/A5) — regenerates that report's 解耦用例 + 总量用例 datasets (default: A3)")
    p.add_argument("--date", default=None,
                   help="Report date string stored in DATA.date, shown in the header/footer (e.g. 2026-09-19)")
    p.add_argument("--total", default=None,
                   help="The report's Total (总量) workbook — builds the 总量用例 scenario (DATA_<R>_TOTAL + cases_<r>_total.js, watch folded in). Omitted, it stays null/blank")
    p.add_argument("--total-status", default=None,
                   help="Tracking workbook for the Total (总量) data (default: summary_report.xlsx next to --total)")
    p.add_argument("--total-date", default=None,
                   help="Report date for the Total (总量) scenario DATA_<R>_TOTAL.date (default: --date)")
    args = p.parse_args(argv)
    args.dataset = args.dataset.upper()

    blacklist = args.blacklist
    if blacklist is None:
        cand = os.path.join(os.path.dirname(args.input) or ".", "blacklist_testcases.xlsx")
        blacklist = cand if os.path.exists(cand) else None

    status_path = args.status
    if status_path is None:
        d = os.path.dirname(args.input) or "."
        for name in ("status_tracking.xlsx", "summary_report.xlsx"):
            cand = os.path.join(d, name)
            if os.path.exists(cand):
                status_path = cand
                break

    total_status = args.total_status
    if args.total and total_status is None:
        d = os.path.dirname(args.total) or "."
        for name in ("summary_report.xlsx", "status_tracking.xlsx"):
            cand = os.path.join(d, name)
            if os.path.exists(cand):
                total_status = cand
                break

    # Should Not Do (社区日落) 文件：其用例从解耦场景的 case-level 数字中剔除，
    # 与总量场景口径一致，使「收集测试用例」「看护用例数」「收集用例执行结果」
    # 均不含 Should Not Do 用例（文件级口径仍保留 SND 文件本身）。
    track = load_status(status_path) if status_path else {}
    snd_files = {f for f, (st, pr, asg) in track.items() if pr == "Should Not Do"}
    data, detail, file_list = build(args.input, blacklist, args.dataset,
                                    exclude_files=snd_files)

    # Attach the tracked status/priority/assignee (if any) to each file list
    # entry, as 5th/6th/7th elements, and keep the pre-collection counts
    # (cpu/npu/pub from all_files) as the final 8th/9th/10th. Files absent from
    # the tracking sheet carry "" for each (untracked). Also folds "Should Not Do"
    # files out of the 未泛化 bucket.
    #
    # 解耦导出的预收集列可能为空（2026-10-08 起），此时用 Total 工作簿的预收集
    # 对齐收集目标与「未泛化/无用例文件」划分，使无用例文件口径与总量用例一致。
    total_precollect = load_precollect(args.total) if args.total else None
    data, file_list = attach_status_and_fold(data, file_list, status_path,
                                             total_precollect=total_precollect)

    if args.date:
        data["date"] = args.date
    data["dataset"] = f"{args.dataset}_DECOUPLE"

    begin, end = data_markers(args.dataset, "DECOUPLE")
    with open(args.output, "r", encoding="utf-8") as f:
        html = f.read()
    html = inject(html, begin, end,
                  "\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n  ")

    # Total (总量) — when --total is supplied, build the report's 总量用例 scenario
    # (DATA_<R>_TOTAL + cases_<r>_total.js). The 看护策略 numbers from build_total()
    # are folded into the scenario object as ``watch`` (one object per scenario; the
    # separate TOTAL_OVERVIEW object is gone).
    if args.total:
        watch_data = build_total(args.total, total_status)
        total_date = args.total_date or args.date

        # Should Not Do (无需泛化/废弃) 文件：其优先级为 "Should Not Do"，对应
        # 用例需从总量场景的各用例数量统计中剔除（社区日落用例），与顶部用例总览口径一致。
        total_track = load_status(total_status) if total_status else {}
        snd_files = {f for f, (st, pr, asg) in total_track.items() if pr == "Should Not Do"}
        tdata, tdetail, tfile_list = build(args.total, None, "TOTAL",
                                           unsupported_to_blacklist=True,
                                           exclude_files=snd_files)
        tdata, tfile_list = attach_status_and_fold(tdata, tfile_list, total_status)
        tdata["watch"] = watch_data.get("watch")
        if total_date:
            tdata["date"] = total_date
        tdata["dataset"] = f"{args.dataset}_TOTAL"
        tbegin, tend = data_markers(args.dataset, "TOTAL")
        html = inject(html, tbegin, tend,
                      "\n" + json.dumps(tdata, ensure_ascii=False, indent=2) + "\n  ")

        tcases_path = os.path.join(
            os.path.dirname(args.output) or ".", cases_filename(args.dataset, "TOTAL"))
        with open(tcases_path, "w", encoding="utf-8") as f:
            f.write("window.CASES=" +
                    json.dumps(tdetail, ensure_ascii=False, separators=(",", ":")) + ";\n")
            f.write("window.FILES=" +
                    json.dumps(tfile_list, ensure_ascii=False, separators=(",", ":")) + ";\n")
        t_n_files = sum(len(files) for files in tdetail.values())
        t_n_cases = sum(len(entries) for files in tdetail.values() for entries in files.values())
        tcases_size = os.path.getsize(tcases_path)
        print(f"[total] files_total={tdata['files_total']}  files_gen={tdata['files_gen']} "
              f"({tdata['files_gen_rate']}%)  files_snd={tdata['files_snd']}  "
              f"cases_total={tdata['cases_total']}  pass_rate={tdata['cases_pass_rate']}%  "
              f"blacklist={tdata['blacklist_total']}")
        print(f"[total] detail modules={len(tdetail)}  files={t_n_files}  cases={t_n_cases}  "
              f"files_list={len(tfile_list)}  -> {tcases_path} ({tcases_size/1024/1024:.2f} MB)")
        for name, sh in tdata["sheets"].items():
            print(f"  [total] {name:14s} files={sh['files']:>3} gen={sh['gen_files']:>2} "
                  f"na={sh['na_files']:>3} cases={sh['gen_cases']:>5} "
                  f"P={sh['passed']:>5} F={sh['failed']:>5} S={sh['skipped']:>4} "
                  f"T={sh['timeout']} E={sh['error']} B={sh['blacklist_unsupported']}")
    else:
        # No --total: DATA_<R>_TOTAL stays null in the template, but still emit a
        # blank cases file so the 总量用例 tab's <script> never 404s.
        blank_cases = os.path.join(
            os.path.dirname(args.output) or ".", cases_filename(args.dataset, "TOTAL"))
        with open(blank_cases, "w", encoding="utf-8") as f:
            f.write("window.CASES={};window.FILES=[];\n")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    cases_path = args.cases_out or os.path.join(
        os.path.dirname(args.output) or ".", cases_filename(args.dataset, "DECOUPLE"))
    with open(cases_path, "w", encoding="utf-8") as f:
        f.write("window.CASES=" +
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + ";\n")
        f.write("window.FILES=" +
                json.dumps(file_list, ensure_ascii=False, separators=(",", ":")) + ";\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Concise verification summary
    n_files = sum(len(files) for files in detail.values())
    n_cases = sum(len(entries) for files in detail.values() for entries in files.values())
    cases_size = os.path.getsize(cases_path)
    print(f"files_total={data['files_total']}  files_gen={data['files_gen']} "
          f"({data['files_gen_rate']}%)  files_snd={data['files_snd']}  "
          f"cases_total={data['cases_total']} "
          f"pass_rate={data['cases_pass_rate']}%  blacklist={data['blacklist_total']}")
    n_tracked = sum(1 for it in file_list if it[4])
    n_priority = sum(1 for it in file_list if it[5])
    n_assignee = sum(1 for it in file_list if it[6])
    print(f"detail modules={len(detail)}  files={n_files}  cases={n_cases}  "
          f"files_list={len(file_list)} (status={n_tracked}, priority={n_priority}, "
          f"assignee={n_assignee})  -> {cases_path} ({cases_size/1024/1024:.2f} MB)")
    for name, sh in data["sheets"].items():
        print(f"  {name:14s} files={sh['files']:>3} gen={sh['gen_files']:>2} "
              f"na={sh['na_files']:>3} cases={sh['gen_cases']:>5} "
              f"P={sh['passed']:>5} F={sh['failed']:>5} S={sh['skipped']:>4} "
              f"T={sh['timeout']} E={sh['error']} B={sh['blacklist_unsupported']}")
    print(f"wrote {args.output} + {cases_path}")


if __name__ == "__main__":
    main()
