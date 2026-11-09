"""Run every test in this directory. `python tests/run_all.py`"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("codec vectors vs the JS oracle", ["test_vectors.py"]),
    ("protocol / spec invariants", ["test_protocol.py"]),
    ("architecture: core over swappable buses", ["test_architecture.py"]),
    ("daemon over a socket pair", ["test_wire.py"]),
    ("daemon over stdio, as a subprocess", ["test_daemon.py"]),
    ("routine files validate", ["smoke.py", "routines/hello.json", "--fast"]),
    ("choreography showcase validates", ["smoke.py", "routines/showcase.json", "--fast"]),
    ("declared duration table", ["test_durations.py"]),
    ("action duration benchmark", ["test_bench.py"]),
    ("seated story and renewal rule", ["test_seated.py"]),
    ("phone player (Web Bluetooth page)", ["test_phone.py"]),
]


def main():
    failed = []
    for label, argv in SUITES:
        script = argv[0]
        path = HERE / script
        if not path.exists():
            print(f"SKIP  {label} ({script} missing)")
            continue
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        result = subprocess.run([sys.executable, "-u", str(path), *argv[1:]], cwd=HERE.parent)
        if result.returncode != 0:
            failed.append(label)
    print(f"\n{'=' * 70}")
    if failed:
        print(f"FAILED: {len(failed)} suite(s)")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"all {len(SUITES)} suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())