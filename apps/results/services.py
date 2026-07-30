"""Rank calculation for results — assignment-scoped.

Ranking lives here rather than on the exam ViewSet because two unrelated flows
need it: submitting an attempt (which adds a row to the cohort) and publishing
results (which is the moment a teacher actually reads the ranks). One
implementation is what stops those two drifting apart.
"""
from __future__ import annotations

import logging

from .models import Result

logger = logging.getLogger(__name__)


def rerank_assignment(assignment_id: int) -> int:
    """Recompute ``rank`` for every result of one assignment.

    Ranks use **standard competition ranking**: equal scores share a rank and the
    next rank skips the numbers used up ("1, 2, 2, 4"). Two students who scored
    identically are genuinely joint — handing one of them 2nd place because their
    row happened to be written first is not a tie-break, it is an arbitrary answer
    presented as a real one. The old sequential 1..N numbering did exactly that.

    The ordering key is marks descending, then time taken ascending, so a faster
    student outranks a slower one on equal marks. Only an exact match on BOTH
    counts as a tie.

    Scope is the assignment, not the test: a test assigned to two sections is two
    cohorts sitting separate windows, and one merged ladder across them would rank
    students against people they never competed with. This matches what
    ``Result.rank``'s help_text already promises.

    Returns the number of rows whose rank actually changed.
    """
    results = list(
        Result.objects
        .filter(assignment_id=assignment_id)
        .order_by('-obtained_marks', 'time_taken_seconds')
        .only('id', 'rank', 'obtained_marks', 'time_taken_seconds')
    )

    changed = []
    previous_key = None
    previous_rank = 0

    for position, result in enumerate(results, start=1):
        key = (result.obtained_marks, result.time_taken_seconds)
        if key == previous_key:
            # Joint with the row above: same rank, while `position` keeps
            # advancing so the next distinct score skips the numbers consumed.
            rank = previous_rank
        else:
            rank = position
            previous_key = key
            previous_rank = rank

        if result.rank != rank:
            result.rank = rank
            changed.append(result)

    if changed:
        Result.objects.bulk_update(changed, ['rank'], batch_size=500)

    return len(changed)
