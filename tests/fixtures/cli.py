"""Call the public checker with its JSON output intact."""

import contextlib
import io
import json

from tools import policy


def invoke(*arguments):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = policy.main(list(arguments))
    return code, json.loads(output.getvalue() or errors.getvalue())
