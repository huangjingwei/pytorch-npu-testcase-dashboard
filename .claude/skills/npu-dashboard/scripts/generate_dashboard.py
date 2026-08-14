#!/usr/bin/env python3
"""Generate the PyTorch NPU test-case dashboard from a raw results workbook.

Reads ``all_testcases.xlsx`` (one sheet per module, one row per file/case record)
and produces two files:

- ``index.html`` — the dashboard shell. Contains the small aggregate ``DATA``
  object (injected between ``__DATA_BEGIN__`` / ``__DATA_END__``) and all
  rendering code, and loads the case details from a sibling file.
- ``cases.js`` — a single ``window.CASES = {...}`` assignment holding the compact
  per-case detail tree (module -> file -> [[nodeid_suffix, result], ...]),
  plus a flat ``window.FILES = [[module, file, gen, cases], ...]`` list for the
  测试文件 tab. This is kept OUT of the HTML so the shell stays small even when
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
from collections import Counter

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl is required: pip3 install openpyxl")

# Sentinel used in the raw data for a row that has no matched nodeid, i.e. a
# file-level (未泛化) record rather than a concrete test case.
UNMATCHED_NODEID = "(未匹配)"

# Execution-status keys the dashboard knows about, in display order.
STATUS_KEYS = ["passed", "failed", "skipped", "timeout", "error"]

# Header names (verbatim) the script looks for, with 0-based positional fallback
# when the header row does not match (robust to column reordering).
COLUMN_SPEC = {
    "file":   {"names": ["File"],   "fallback": 2},
    "nodeid": {"names": ["nodeid"], "fallback": 3},
    "result": {"names": ["执行结果"], "fallback": 4},
}

DATA_BEGIN = "/*__DATA_BEGIN__*/"
DATA_END = "/*__DATA_END__*/"


def _find_column(headers, spec):
    for i, h in enumerate(headers):
        if h is not None and str(h).strip() in spec["names"]:
            return i
    return spec["fallback"]


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    return v if v is None else str(v).strip()


def is_matched(nodeid):
    """True when a row represents a concrete, generalized test case."""
    return bool(nodeid) and nodeid != UNMATCHED_NODEID


def build(path):
    """One pass over the workbook -> (aggregate DATA dict, per-case detail tree,
    flat file list)."""
    # NOTE: not read_only — this workbook reports broken dimension metadata in
    # read-only mode (max_row=1), which would silently truncate iteration.
    wb = openpyxl.load_workbook(path, data_only=True)

    all_files = set()
    all_gen_files = set()
    case_totals = Counter()
    sheets = {}
    detail = {}
    file_list = []  # [[module, file, gen(0/1), case_count], ...] for the 测试文件 tab

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

        for row in rows:
            f = _cell(row, col_file)
            nodeid = _cell(row, col_nodeid)
            result = _cell(row, col_result)

            if not f:
                continue  # skip blank file cell

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
            "timeout": status["timeout"],
            "error": status["error"],
            "na_files": len(files) - len(gen_files),
        }
        if module_detail:
            detail[ws.title] = module_detail
        for f in sorted(files):
            file_list.append([ws.title, f, 1 if f in gen_files else 0,
                              file_cases.get(f, 0)])

    files_total = len(all_files)
    files_gen = len(all_gen_files)
    files_na = files_total - files_gen
    cases_total = sum(case_totals.values())

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
            "timeout": case_totals["timeout"],
            "error": case_totals["error"],
        },
        "cases_pass_rate": round(case_totals["passed"] / cases_total * 100, 1) if cases_total else 0.0,
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


def main(argv=None):
    p = argparse.ArgumentParser(description="Regenerate the NPU test dashboard.")
    p.add_argument("--input", "-i", default="all_testcases.xlsx",
                   help="Input workbook (default: all_testcases.xlsx)")
    p.add_argument("--output", "-o", default="index.html",
                   help="Output/template HTML (default: index.html)")
    p.add_argument("--cases-out", "-c", default=None,
                   help="Where to write the case-detail JS (default: <output dir>/cases.js)")
    p.add_argument("--json-out", help="Optional: also write DATA to a .json file")
    args = p.parse_args(argv)

    data, detail, file_list = build(args.input)

    with open(args.output, "r", encoding="utf-8") as f:
        html = f.read()
    html = inject(html, DATA_BEGIN, DATA_END,
                  "\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n  ")
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    cases_path = args.cases_out or os.path.join(os.path.dirname(args.output) or ".", "cases.js")
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
          f"({data['files_gen_rate']}%)  cases_total={data['cases_total']} "
          f"pass_rate={data['cases_pass_rate']}%")
    print(f"detail modules={len(detail)}  files={n_files}  cases={n_cases}  "
          f"files_list={len(file_list)}  -> {cases_path} ({cases_size/1024/1024:.2f} MB)")
    for name, sh in data["sheets"].items():
        print(f"  {name:14s} files={sh['files']:>3} gen={sh['gen_files']:>2} "
              f"na={sh['na_files']:>3} cases={sh['gen_cases']:>5} "
              f"P={sh['passed']:>5} F={sh['failed']:>5} S={sh['skipped']:>4} "
              f"T={sh['timeout']} E={sh['error']}")
    print(f"wrote {args.output} + {cases_path}")


if __name__ == "__main__":
    main()
