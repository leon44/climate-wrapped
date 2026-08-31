"""Per-request phase timing, logged as a single summary line per request so
we can see where time goes in the data pipeline without spamming the logs
(see app/routes.py::wrapped and app/stats.py::compute_stats)."""

import time
from contextlib import contextmanager


class Stopwatch:
    def __init__(self):
        self.splits: dict[str, float] = {}

    @contextmanager
    def split(self, label: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.splits[label] = time.perf_counter() - start

    def summary(self) -> str:
        parts = " ".join(f"{k}={v:.3f}s" for k, v in self.splits.items())
        return f"{parts} total={sum(self.splits.values()):.3f}s"
