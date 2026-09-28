"""Call the public checker with its JSON output intact."""

import contextlib
import io
import json
import sys

from tools import policy


def invoke(*arguments):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = policy.main(list(arguments))
    return code, json.loads(output.getvalue() or errors.getvalue())


def checker_command(data):
    """Return argv that runs the checker script with DATA_ROOT set to data.

    Use it where the checker must run as a separate process, for example when
    Nix output goes to the real standard error.
    """
    tools = policy.SOURCE_ROOT / "tools"
    script = (
        "import pathlib, runpy, sys\n"
        f"sys.path.insert(0, {str(tools)!r})\n"
        "import records\n"
        f"records.DATA_ROOT = pathlib.Path({str(data)!r})\n"
        f"runpy.run_path({str(tools / 'policy.py')!r}, run_name='__main__')\n"
    )
    return [sys.executable, "-c", script]
