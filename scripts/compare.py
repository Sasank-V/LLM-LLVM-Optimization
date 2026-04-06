import subprocess
import time
import os


SOURCE = "test.c"
BASE_IR = "./ll-files/.base.ll"

# manual pass pipelines
custom_pipelines = {
    "baseline": "",
    "const_prop": "sccp",
    "combine_dce": "instcombine,dce",
    "inline_first": "cgscc(inline),function(sccp)",
    "sccp_first": "function(sccp),cgscc(inline)",
}

# built-in LLVM optimization pipelines
opt_levels = ["O1", "O2", "O3"]


def run(cmd):
    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip())
        raise subprocess.CalledProcessError(result.returncode, cmd)


print("Generating LLVM IR...")

run(f"clang -O0 -S -emit-llvm {SOURCE} -o {BASE_IR}")

results = []

# ---------- CUSTOM PIPELINES ----------
for name, passes in custom_pipelines.items():

    opt_ir = f"./ll-files/{name}.ll"
    exe = f"./ll-files/{name}.out"

    if passes == "":
        run(f"cp {BASE_IR} {opt_ir}")
    else:
        run(f"opt -passes='{passes}' {BASE_IR} -S -o {opt_ir}")

    run(f"clang {opt_ir} -O0 -lm -o {exe}")

    start = time.time()
    subprocess.run(
        f"./{exe}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    runtime = time.time() - start

    size = os.path.getsize(exe)

    results.append((name, passes, size, runtime))


# ---------- LLVM BUILT-IN PIPELINES ----------
for level in opt_levels:

    opt_ir = f"./ll-files/{level}.ll"
    exe = f"./ll-files/{level}.out"

    run(f"opt -{level} {BASE_IR} -S -o {opt_ir}")
    run(f"clang {opt_ir} -O0 -lm -o {exe}")

    start = time.time()
    subprocess.run(
        f"./{exe}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    runtime = time.time() - start

    size = os.path.getsize(exe)

    results.append((level, f"default {level} pipeline", size, runtime))


print("\n===== PIPELINE RESULTS =====\n")

for r in results:
    print(f"{r[0]:12} | {r[1]:25} | size={r[2]} bytes | time={r[3]:.6f}s")
