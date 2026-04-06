import argparse
import ast
import csv
import os
import shutil
import subprocess
import time
from pathlib import Path


def run_cmd(args, cwd=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode == 0, result.stdout, result.stderr


def load_pipelines(compare_py_path):
    source = compare_py_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    custom_pipelines = None
    opt_levels = None

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "custom_pipelines":
                custom_pipelines = ast.literal_eval(node.value)
            if isinstance(target, ast.Name) and target.id == "opt_levels":
                opt_levels = ast.literal_eval(node.value)

    if not isinstance(custom_pipelines, dict) or not isinstance(opt_levels, list):
        raise ValueError("Could not read custom_pipelines/opt_levels from compare.py")

    return custom_pipelines, opt_levels


def discover_sources(suite_dir, extensions):
    files = []
    for path in suite_dir.rglob("*"):
        if path.is_file() and path.suffix in extensions:
            files.append(path)
    files.sort()
    return files


def safe_rel_id(path, root):
    rel = path.relative_to(root)
    return str(rel).replace(os.sep, "__")


def test_source_file(src_path, suite_dir, work_dir, custom_pipelines, opt_levels):
    rows = []
    src_dir = src_path.parent
    src_name = src_path.name
    test_id = safe_rel_id(src_path, suite_dir)
    test_dir = work_dir / test_id
    test_dir.mkdir(parents=True, exist_ok=True)

    base_ir = test_dir / "base.ll"

    ok, _, err = run_cmd(
        ["clang", "-O0", "-S", "-emit-llvm", src_name, "-o", str(base_ir)], cwd=src_dir
    )
    if not ok:
        rows.append(
            {
                "source": str(src_path.relative_to(suite_dir)),
                "pipeline": "base_ir",
                "status": "fail",
                "reason": err.strip().replace("\n", " | "),
                "exe_size": "",
                "compile_time_s": "",
            }
        )
        return rows

    pipeline_specs = []
    for name, passes in custom_pipelines.items():
        pipeline_specs.append((name, "custom", passes))
    for level in opt_levels:
        pipeline_specs.append((level, "builtin", level))

    for name, kind, spec in pipeline_specs:
        opt_ir = test_dir / f"{name}.ll"
        exe = test_dir / f"{name}.out"

        t0 = time.time()

        if kind == "custom" and spec == "":
            try:
                shutil.copyfile(base_ir, opt_ir)
                opt_ok, opt_err = True, ""
            except OSError as copy_err:
                opt_ok, opt_err = False, str(copy_err)
        elif kind == "custom":
            opt_ok, _, opt_err = run_cmd(
                ["opt", f"-passes={spec}", str(base_ir), "-S", "-o", str(opt_ir)]
            )
        else:
            opt_ok, _, opt_err = run_cmd(
                ["opt", f"-{spec}", str(base_ir), "-S", "-o", str(opt_ir)]
            )

        if not opt_ok:
            rows.append(
                {
                    "source": str(src_path.relative_to(suite_dir)),
                    "pipeline": name,
                    "status": "fail",
                    "reason": f"opt failed: {opt_err.strip().replace(chr(10), ' | ')}",
                    "exe_size": "",
                    "compile_time_s": f"{time.time() - t0:.6f}",
                }
            )
            continue

        link_ok, _, link_err = run_cmd(
            ["clang", str(opt_ir), "-O0", "-lm", "-o", str(exe)]
        )
        elapsed = time.time() - t0
        if not link_ok:
            rows.append(
                {
                    "source": str(src_path.relative_to(suite_dir)),
                    "pipeline": name,
                    "status": "fail",
                    "reason": f"link failed: {link_err.strip().replace(chr(10), ' | ')}",
                    "exe_size": "",
                    "compile_time_s": f"{elapsed:.6f}",
                }
            )
            continue

        rows.append(
            {
                "source": str(src_path.relative_to(suite_dir)),
                "pipeline": name,
                "status": "ok",
                "reason": "",
                "exe_size": str(exe.stat().st_size),
                "compile_time_s": f"{elapsed:.6f}",
            }
        )

    return rows


def summarize(results):
    total = len(results)
    ok = sum(1 for row in results if row["status"] == "ok")
    fail = total - ok

    by_pipeline = {}
    for row in results:
        name = row["pipeline"]
        if name == "base_ir":
            continue
        if name not in by_pipeline:
            by_pipeline[name] = {"ok": 0, "fail": 0}
        by_pipeline[name]["ok" if row["status"] == "ok" else "fail"] += 1

    return total, ok, fail, by_pipeline


def summarize_changes_vs_baseline(results):
    by_source = {}
    for row in results:
        source = row["source"]
        if source not in by_source:
            by_source[source] = {}
        by_source[source][row["pipeline"]] = row

    per_pipeline = {}
    for source_rows in by_source.values():
        baseline = source_rows.get("baseline")
        if not baseline or baseline["status"] != "ok":
            continue

        baseline_size = int(baseline["exe_size"])
        baseline_time = float(baseline["compile_time_s"])

        for pipeline, row in source_rows.items():
            if pipeline in ("baseline", "base_ir"):
                continue
            if row["status"] != "ok":
                continue

            curr_size = int(row["exe_size"])
            curr_time = float(row["compile_time_s"])

            size_delta = curr_size - baseline_size
            time_delta = curr_time - baseline_time

            if pipeline not in per_pipeline:
                per_pipeline[pipeline] = {
                    "count": 0,
                    "size_delta_sum": 0,
                    "size_pct_sum": 0.0,
                    "time_delta_sum": 0.0,
                    "time_pct_sum": 0.0,
                }

            per_pipeline[pipeline]["count"] += 1
            per_pipeline[pipeline]["size_delta_sum"] += size_delta
            per_pipeline[pipeline]["size_pct_sum"] += (
                (size_delta / baseline_size) * 100.0 if baseline_size else 0.0
            )
            per_pipeline[pipeline]["time_delta_sum"] += time_delta
            per_pipeline[pipeline]["time_pct_sum"] += (
                (time_delta / baseline_time) * 100.0 if baseline_time else 0.0
            )

    return per_pipeline


def build_pipeline_display(custom_pipelines, opt_levels):
    display = {}
    for name, passes in custom_pipelines.items():
        display[name] = passes
    for level in opt_levels:
        display[level] = f"default {level} pipeline"
    display["base_ir"] = "base IR generation"
    return display


def main():
    parser = argparse.ArgumentParser(
        description="Test compare.py LLVM pipelines across llvm-test-suite source files."
    )
    parser.add_argument(
        "--suite-dir",
        default="./llvm-test-suite",
        help="Path to llvm-test-suite directory",
    )
    parser.add_argument(
        "--subdir",
        default="SingleSource",
        help="Subdirectory inside suite-dir to scan (default: SingleSource)",
    )
    parser.add_argument(
        "--compare-file",
        default="./compare.py",
        help="Path to compare.py containing pipeline definitions",
    )
    parser.add_argument(
        "--work-dir",
        default="./ll-files/llvm-suite",
        help="Directory for generated IR/executables",
    )
    parser.add_argument(
        "--report",
        default="./ll-files/llvm-suite-report.csv",
        help="CSV output report path",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit number of source files (0 = all)",
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        default=[".c"],
        help="Source file extensions to test",
    )

    args = parser.parse_args()

    suite_dir = Path(args.suite_dir).resolve()
    source_root = (suite_dir / args.subdir).resolve() if args.subdir else suite_dir
    compare_file = Path(args.compare_file).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_file = Path(args.report).resolve()

    custom_pipelines, opt_levels = load_pipelines(compare_file)
    pipeline_display = build_pipeline_display(custom_pipelines, opt_levels)
    sources = discover_sources(source_root, set(args.ext))
    if args.max_files > 0:
        sources = sources[: args.max_files]

    if not sources:
        print("No source files found.")
        return

    work_dir.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    print(f"Scanning: {source_root}")
    print(f"Found {len(sources)} source files. Running pipelines...")

    for idx, src in enumerate(sources, start=1):
        rel = src.relative_to(source_root)
        print(f"[{idx}/{len(sources)}] {rel}")
        rows = test_source_file(
            src, source_root, work_dir, custom_pipelines, opt_levels
        )
        all_rows.extend(rows)

        print(f"\n===== PIPELINE RESULTS: {rel} =====\n")
        for row in rows:
            pipeline = row["pipeline"]
            passes = pipeline_display.get(pipeline, "")
            if row["status"] == "ok":
                size = row["exe_size"]
                elapsed = float(row["compile_time_s"])
                print(
                    f"{pipeline:12} | {passes:25} | size={size} bytes | time={elapsed:.6f}s"
                )
            else:
                elapsed = row["compile_time_s"]
                if elapsed:
                    print(
                        f"{pipeline:12} | {passes:25} | status=fail | time={elapsed}s | {row['reason']}"
                    )
                else:
                    print(
                        f"{pipeline:12} | {passes:25} | status=fail | {row['reason']}"
                    )
        print()

    fields = ["source", "pipeline", "status", "reason", "exe_size", "compile_time_s"]
    with report_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    total, ok, fail, by_pipeline = summarize(all_rows)
    print("\n===== LLVM TEST-SUITE PIPELINE SUMMARY =====")
    print(f"Total checks: {total}")
    print(f"Passed: {ok}")
    print(f"Failed: {fail}")
    print("\nPer pipeline:")
    for name in sorted(by_pipeline.keys()):
        print(
            f"  {name:12} ok={by_pipeline[name]['ok']:4} fail={by_pipeline[name]['fail']:4}"
        )

    changes = summarize_changes_vs_baseline(all_rows)
    print("\n===== FINAL CHANGE REPORT (vs baseline) =====")
    if not changes:
        print("No comparable results (baseline + pipeline both passing) were found.")
    else:
        print(
            "pipeline      | compared_files | avg_size_delta(bytes) | avg_size_delta(%) | avg_compile_time_delta(s) | avg_compile_time_delta(%)"
        )
        for name in sorted(changes.keys()):
            item = changes[name]
            count = item["count"]
            avg_size_delta = item["size_delta_sum"] / count
            avg_size_pct = item["size_pct_sum"] / count
            avg_time_delta = item["time_delta_sum"] / count
            avg_time_pct = item["time_pct_sum"] / count
            print(
                f"{name:12} | {count:14} | {avg_size_delta:21.2f} | {avg_size_pct:16.2f} | {avg_time_delta:24.6f} | {avg_time_pct:21.2f}"
            )

    print(f"\nDetailed CSV report: {report_file}")


if __name__ == "__main__":
    main()
