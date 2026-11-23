"""One-command entry points (Windows-friendly replacement for a Makefile).

Usage:  python tasks.py <command>
  data    generate synthetic data, rate documents and ground truth   (Phase 1)
  test    run the test suite
  evals   run the evaluation suite in replay mode                     (Phase 4)
  app     start the Streamlit demo UI                                 (Phase 5)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def _run(args: list[str]) -> int:
    return subprocess.call(args, cwd=ROOT)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    py = sys.executable
    if cmd == "test":
        return _run([py, "-m", "pytest"])
    if cmd == "data":
        return _run([py, "-m", "data_gen.generate"])
    if cmd == "evals":
        return _run([py, "-m", "evals.run_evals", "--replay"])
    if cmd == "app":
        return _run([py, "-m", "streamlit", "run", "app/Home.py"])
    print(f"Unknown command: {cmd}\n{__doc__}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
