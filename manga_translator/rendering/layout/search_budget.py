"""Page-local search policy; direct solver calls retain their original breadth."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
import math


class SearchDeadlineReached(Exception):
    """Unwind unfinished DP work without discarding completed candidates."""


@dataclass
class PageSearchBudget:
    started: float = field(default_factory=perf_counter)
    target_seconds: float = 5.0
    unresolved_seconds: float = 10.0
    deadline_seconds: float = 30.0
    phase: str = "initial"
    exhausted: bool = False
    rescue_usage: int = 0
    phases: list = field(default_factory=lambda: ["initial"])

    @property
    def initial(self):
        return self.phase == "initial"

    def expired(self):
        if perf_counter() - self.started >= self.deadline_seconds:
            self.exhausted = True
        return self.exhausted

    def check(self):
        return self.expired()

    def check_dp(self):
        if self.expired():
            raise SearchDeadlineReached()

    def may_continue(self, validated=False, conflicting=False):
        """After the target, retain completed initial work; after 10s, only repairs."""
        if self.expired():
            return False
        elapsed = perf_counter() - self.started
        if validated and not conflicting:
            if elapsed >= self.unresolved_seconds:
                return False
            if self.initial and elapsed >= self.target_seconds:
                return False
        return True

    @contextmanager
    def searching(self, phase):
        previous = self.phase
        self.phase = phase
        if phase not in self.phases:
            self.phases.append(phase)
        try:
            yield
        finally:
            self.phase = previous

    def to_dict(self):
        return {"phase": "exhausted" if self.exhausted else self.phase,
                "elapsed_seconds": perf_counter() - self.started, "phases": list(self.phases),
                "budget_exhausted": self.exhausted, "rescue_usage": self.rescue_usage,
                "target_seconds": self.target_seconds, "unresolved_seconds": self.unresolved_seconds,
                "deadline_seconds": self.deadline_seconds}


_SEARCH_BUDGET = ContextVar("layout_search_budget", default=None)


def get_search_budget():
    return _SEARCH_BUDGET.get()


@contextmanager
def page_search_budget():
    budget = PageSearchBudget()
    token = _SEARCH_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _SEARCH_BUDGET.reset(token)


def small_text_rescue_minimum(region, config, image_shape):
    from ...utils import is_preserved_region
    if is_preserved_region(region) or (config.font_size is not None and config.font_size > 0):
        return None
    configured = config.font_size_minimum
    if configured == -1:
        configured = round(sum(image_shape[:2]) / 200)
    return max(1, int(configured), math.ceil(8 * max(image_shape[:2]) / 2048))
