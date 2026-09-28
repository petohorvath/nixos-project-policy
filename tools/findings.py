"""Findings and rule statuses shared by the input and public-output rules."""


def finding(rule, level, message, **subject):
    """Return one finding; SUBJECT names what it is about, such as `input`."""
    return {"rule": rule, **subject, "level": level, "message": message}


def summarize(findings, rules):
    """Return each rule's status: the worst finding level, or pass."""
    status = dict.fromkeys(rules, "pass")
    for item in findings:
        if item["level"] == "fail" or status[item["rule"]] == "pass":
            status[item["rule"]] = item["level"]
    return status
