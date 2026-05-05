import threading
import time
from dataclasses import dataclass


ISSUE_ORDER = ("hotkey", "clipboard_listener", "clipboard_read")


@dataclass(frozen=True)
class RuntimeIssue:
    key: str
    title: str
    detail: str = None
    error_code: int = None
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", time.time())


class RuntimeStatusStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._issues = {}

    def set_issue(self, key, title, detail=None, error_code=None, timestamp=None):
        issue = RuntimeIssue(
            key=key,
            title=title,
            detail=detail,
            error_code=error_code,
            timestamp=timestamp,
        )
        with self._lock:
            self._issues[key] = issue
        return issue

    def clear_issue(self, key):
        with self._lock:
            self._issues.pop(key, None)

    def snapshot(self):
        with self._lock:
            issues = list(self._issues.values())
        return tuple(sorted(issues, key=_issue_sort_key))


def _issue_sort_key(issue):
    try:
        return (ISSUE_ORDER.index(issue.key), issue.key)
    except ValueError:
        return (len(ISSUE_ORDER), issue.key)


def format_status_title(snapshot):
    issues = tuple(snapshot)
    if not issues:
        return "OK"
    if len(issues) == 1:
        return issues[0].title
    return f"{len(issues)} issues"


def format_popup_status(snapshot):
    issues = tuple(snapshot)
    if not issues:
        return ""
    return "Status: " + format_status_title(issues)


def format_tray_status(snapshot):
    issues = tuple(snapshot)
    if not issues:
        return ""
    parts = []
    for issue in issues:
        text = issue.title
        if issue.error_code is not None:
            text = f"{text} ({issue.error_code})"
        parts.append(text)
    return "Status: " + "; ".join(parts)
