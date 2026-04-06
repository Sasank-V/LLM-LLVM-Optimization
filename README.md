# LLVM Pass Sequence Optimization via LLM Finetuning

A system for generating high-quality finetuning datasets and benchmarking LLMs on predicting optimal LLVM optimization pass sequences for code-size reduction.

## Project Overview

This project addresses the problem of selecting optimal LLVM optimization passes for minimizing compiled executable size. Rather than relying on LLVM's fixed optimization levels (O1, O2, O3), we:

1. **Generate ground-truth data** by exhaustively testing hundreds of pass combinations on real benchmark files
2. **Finetune LLMs** to predict the best pass sequence given LLVM IR
3. **Benchmark predictions** against LLVM's standard optimizations

### Key Objectives

- **Primary:** Minimize final executable size
- **Secondary:** Preserve compilability and correctness
- **Tertiary:** Keep optimization pipelines concise

## Repository Structure

```
.
├── llvm_pass_finetune_dataset_builder.ipynb    # Generate training datasets
├── llm_llvm_benchmark.ipynb                     # Benchmark LLM predictions
├── compare.py                                    # Predefined pass pipelines
├── llvm-test.py                                  # Utility scripts
├── test.c                                        # Test files
├── ll-files/                                     # Generated LLVM IR files
│   ├── baseline.ll, O1.ll, O2.ll, O3.ll ...
│   ├── final_report.csv                         # Benchmark results
│   └── llm-bench/                               # Per-model benchmark runs
└── llvm-test-suite/                             # LLVM test suite (source discovery)
    ├── SingleSource/                            # Cloned from https://github.com/llvm/llvm-test-suite
    ├── MultiSource/
    └── ...
```

## Quick Start

### Prerequisites

- **Python 3.10+**
- **LLVM/Clang** (with `clang`, `opt`, `llc` available in PATH)
- **Jupyter** (for notebook execution)
- **LLVM Test Suite** (clone from https://github.com/llvm/llvm-test-suite)
  ```bash
  git clone https://github.com/llvm/llvm-test-suite.git
  ```
- **Required Python packages:**
  ```bash
  pip install jupyter tqdm torch transformers bitsandbytes matplotlib seaborn
  ```

### Step 1: Generate Training Dataset

Open and run `llvm_pass_finetune_dataset_builder.ipynb`:

1. **Cell 3 (Configuration):**
   - `SOURCE_SUBDIR`: Filter to subset (e.g., `"SingleSource/Benchmarks"`) or empty for all
   - `MAX_FILES`: Cap file count (0 = no limit)
   - `INCLUDE_SINGLE_PASS_CANDIDATES`: Test individual passes (default: True)
   - `INCLUDE_PAIR_PASS_CANDIDATES`: Test ordered pass pairs (default: True)
   - `SUBPROCESS_TIMEOUT_S`: Per-compilation timeout in seconds (default: 45s)

2. **Run Cells 4–11:**
   - Scans `llvm-test-suite` for C/C++ sources
   - Compiles each to base LLVM IR via `clang -O0 -emit-llvm`
   - Tests **100–300+ pass combinations per file** (custom + opt-levels + singles + pairs)
   - Selects best by executable size
   - Exports family-aware train/val/test splits in chat format

3. **Outputs:**
   - `labels_best_pipeline.csv`: Per-file best results
   - `train.jsonl`, `val.jsonl`, `test.jsonl`: Ready for LLM finetuning

**Estimated Runtime:** ~2–8 hours for full llvm-test-suite (depends on file count & hardware)

### Step 2: Benchmark LLM Predictions

Open and run `llm_llvm_benchmark.ipynb`:

1. **Cell 1–10:** Configure model list and compile baselines (LLVM O1, O2, O3, custom pipelines)

2. **Cell 18:** Merge all results into unified report

3. **Cell 19+:** Visualization
   - **Cell 37:** Per-file parity scatter (LLM predictions vs LLVM O3 baseline)
   - **Cell 38+:** Aggregate statistics (median size reduction, speedup, failure rate)

**Typical Workflow:**

- Dataset-builder runs once (or resumed with `RESUME=True`)
- Benchmark notebook runs per LLM model tested

## Key Components

### Dataset Builder (`llvm_pass_finetune_dataset_builder.ipynb`)

**Core Logic:**

- **`compile_to_base_ir(src)`:** Converts C → LLVM IR via clang -O0
- **`apply_pipeline(ir, recipe, passes, opt_level)`:** Applies pass sequence via opt, then linking
- **`candidate_set()`:** Generates deterministic search space:
  - 5–8 custom pipelines (from `compare.py`)
  - 6 standard opt-levels (O0–Oz)
  - 16 single-pass options
  - ~240 ordered pass-pair combos (configurable)
  - Optional random seeds

- **Chat Template (Strict):**

  ```
  System: "You are an LLVM optimization expert. Output ONLY a pass pipeline string."
  User: "Given this IR snippet, choose the best passes. Allowed: [list]. Do not explain."
  Assistant: "mem2reg,instcombine,simplifycfg" or "default_O2"
  ```

- **Output Normalization:** Validates pass names, deduplicates, maps to safe set (16 passes)

**Safe Pass Pool:**

```
mem2reg, instcombine, simplifycfg, gvn, licm, loop-unroll, dce,
inline, sccp, adce, tailcallelim, jump-threading, early-cse, sroa,
reassociate, loop-rotate, indvars
```

### Benchmark (`llm_llvm_benchmark.ipynb`)

**Workflow:**

1. Load LLM (e.g., Qwen 3.5, Nanbeige 4.1) with 4-bit quantization
2. For each source in llvm-test-suite:
   - Pass IR snippet to LLM
   - Parse predicted pass sequence
   - Compile via `opt + clang link`
   - Measure: executable size, compile time, success/failure
3. Compare results against LLVM baselines (O1, O2, O3, custom)
4. Generate scatter plots, heatmaps, statistical summaries

**Models Tested:**

- Qwen 3.5 0.8B (reasoning / non-reasoning)
- Qwen 3.5 2B (reasoning / non-reasoning)
- Nanbeige 4.1 3B

## Output Files

| File                                     | Purpose                                           |
| ---------------------------------------- | ------------------------------------------------- |
| `labels_best_pipeline.csv`               | Best pass sequence per source file + metadata     |
| `labels_best_pipeline.jsonl`             | Structured metadata for result tracking           |
| `candidate_trials.csv`                   | Full trial history (all candidates tested)        |
| `train.jsonl`, `val.jsonl`, `test.jsonl` | Chat-format finetuning data                       |
| `final_report.csv`                       | Merged benchmark results (all models + baselines) |
| `llm-bench/{model}/`                     | Per-model benchmark outputs                       |

### CSV Schema

**labels_best_pipeline.csv:**

```
source, family, status, best_label, best_kind, best_pass_pipeline,
best_opt_level, best_exe_size_b, best_elapsed_s, n_candidates_tested,
error, ir_snippet, source_sha1
```

**JSONL (train.jsonl):**

```json
{
  "messages": [
    { "role": "system", "content": "You are an LLVM optimization expert..." },
    { "role": "user", "content": "Task: choose pipeline. Source: ... IR: ..." },
    { "role": "assistant", "content": "mem2reg,instcombine,simplifycfg" }
  ],
  "meta": {
    "source": "source/file.c",
    "family": "Benchmarks__Misc",
    "split": "train",
    "best_pass_pipeline": "mem2reg,instcombine",
    "best_exe_size_b": 12345,
    "template_version": "v2_strict_pipeline_contract"
  }
}
```

## Configuration Reference

### Dataset Builder (Cell 3)

| Parameter                        | Type | Default | Notes                                              |
| -------------------------------- | ---- | ------- | -------------------------------------------------- |
| `SOURCE_SUBDIR`                  | str  | `""`    | Scan subdirectory of llvm-test-suite (empty = all) |
| `MAX_FILES`                      | int  | `0`     | Max files to process (0 = no limit)                |
| `INCLUDE_SINGLE_PASS_CANDIDATES` | bool | `True`  | Test each pass individually                        |
| `INCLUDE_PAIR_PASS_CANDIDATES`   | bool | `True`  | Test ordered pass pairs                            |
| `MAX_PAIR_PASS_CANDIDATES`       | int  | `0`     | Cap pair combinations (0 = all ~240)               |
| `INCLUDE_RANDOM_CANDIDATES`      | bool | `False` | Add random pipeline seeds                          |
| `SUBPROCESS_TIMEOUT_S`           | int  | `45`    | Timeout per compilation step                       |
| `RUN_EXECUTABLE`                 | bool | `False` | Execute compiled binaries to verify correctness    |
| `RESUME`                         | bool | `True`  | Skip already-processed sources                     |

### Benchmark (Cell 1)

| Parameter           | Type | Notes                                               |
| ------------------- | ---- | --------------------------------------------------- |
| `MODELS`            | list | LLM model identifiers (auto-load from Hugging Face) |
| `QUANTIZATION_BITS` | int  | 4 or 8; fewer bits = faster but lower precision     |
| `DEVICE_MAP`        | str  | `"auto"` to distribute across GPU/CPU               |

## Example Usage

### Generate subset dataset (SingleSource only)

```python
# Cell 3: Set
SOURCE_SUBDIR = "SingleSource/Benchmarks"
MAX_FILES = 50  # First 50 files
SUBPROCESS_TIMEOUT_S = 60

# Then run Cells 4–11
```

### Resume interrupted dataset

```python
# Cell 3: Set
RESUME = True  # Skips already-labeled files in labels_best_pipeline.csv

# Run from Cell 4 again; picks up where left off
```

### Benchmark single model

```python
# Cell 1: Set
MODELS = ["Qwen/Qwen2.5-1.5B"]  # Single model only
QUANTIZATION_BITS = 4

# Run Cells 2–37 for single-model benchmark
```

## Expected Results

On LLVM-test-suite (full run):

- **Dataset Size:** 500–2000 training records (depending on file count)
- **Average Exe Size Reduction:** 5–15% vs LLVM O2, 2–8% vs O3
- **Best Case:** 30%+ reduction on specific passes
- **Compilation Time:** 20–40ms per pass sequence (varies by source)
- **Model Accuracy:** 60–80% within 10% of optimal on validation set

## Troubleshooting

### Empty plots in benchmark notebook

**Cause:** Source path format mismatch (e.g., `./src.c` vs `src.c`)  
**Fix:** Cell 37 includes `canonical_source_key()` for path normalization. If still empty, check:

- Cell 18 merge output (run `final_report.csv` first)
- Baseline baseline selection (prints which LLVM variant was used)

### Pandas import crash

**Cause:** Environment conda issue  
**Fix:** Dataset builder uses pure Python (no pandas) — remove any stray pandas imports

### Compilation timeout

**Cause:** Complex source + many passes + slow hardware  
**Fix:** Increase `SUBPROCESS_TIMEOUT_S` or filter to simpler sources via `SOURCE_SUBDIR`

### Missing LLVM tools

**Cause:** `clang`, `opt` not in PATH  
**Fix:**

```bash
# macOS
brew install llvm

# Ubuntu/Debian
apt-get install clang llvm-dev

# Verify
clang --version
opt --version
```

## References

- [LLVM Optimization Passes](https://llvm.org/docs/Passes/)
- [LLVM Test Suite](https://llvm.org/docs/TestSuiteGuide/)
- [Hugging Face Transformers](https://huggingface.co/docs/transformers/)

---

**Authors:** VIT Compiler Design Course  
**Date:** April 2026  
**Status:** Active Development
