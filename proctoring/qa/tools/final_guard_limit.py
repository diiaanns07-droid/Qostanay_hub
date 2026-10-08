"""QA launcher adapter: real, unchanged native helper, hard limit TWO minutes.

No hook is implemented here. stdin/stdout, parent PID, mode and emergency escape
remain the product helper's protocol. Used only for the captain-authorized QA.
"""
import os
from pathlib import Path
import runpy
import sys


def main():
    helper = Path(os.environ["QORGAU_QA_REAL_HELPER"]).resolve(strict=True)
    if helper == Path(__file__).resolve():
        raise RuntimeError("QA adapter cannot target itself")
    arguments = sys.argv[1:]
    if "--self-check" not in arguments:
        # argparse takes the last explicit option; never trust the shell's 240-minute default.
        arguments += ["--max-minutes", "2"]
    sys.argv = [str(helper), *arguments]
    sys.path.insert(0, str(helper.parent))
    runpy.run_path(str(helper), run_name="__main__")


if __name__ == "__main__":
    main()
