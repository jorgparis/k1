"""Shared helpers for the test suites: quiet logging and a pass/fail reporter.

Several tests deliberately feed the library bad input (out-of-range joints,
corrupt frames, unknown actions) and the library correctly warns about each one.
Silencing the `k1.*` loggers keeps expected warnings out of the test output
without hiding real failures.
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252, which cannot represent every character in
# a robot name or joint label. Reconfigure so a test never dies on a print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

logging.getLogger("k1").setLevel(logging.CRITICAL)
logging.getLogger("k1.protocol").setLevel(logging.CRITICAL)
logging.getLogger("k1.core").setLevel(logging.CRITICAL)
logging.getLogger("k1.bus").setLevel(logging.CRITICAL)
logging.getLogger("k1.wire").setLevel(logging.CRITICAL)


class Report:
    def __init__(self, title: str):
        self.title = title
        self.failures = 0
        self.checks = 0

    def check(self, label: str, ok: bool, extra: str = ""):
        self.checks += 1
        self.failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  {extra}" if extra else ""))
        return ok

    def section(self, label: str):
        print(f"\n-- {label} --")

    def finish(self) -> int:
        verdict = "ALL PASS" if not self.failures else f"{self.failures} FAILURE(S)"
        print(f"\n{self.title}: {self.checks} checks, {verdict}")
        return 1 if self.failures else 0