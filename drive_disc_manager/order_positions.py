"""Pure position bookkeeping for an acquisition-order warehouse pass. Nothing here touches the game."""
from collections import Counter, defaultdict

from .store import fingerprint


def locate(seen, known):
    """Where the screen starts, judged only by discs on it whose warehouse position is already known.

    seen:  {screen offset: disc reading} for the discs read on this screen, offset counted from 0.
    known: {position: disc reading} for every disc whose position is established, counted from 1 at the newest.
    The screen's first cell is position start + 1. A disc known at position q seen at offset o therefore
    votes for a start of q - o - 1. Returns the start, or None when nothing on screen is known yet or two
    starts are equally supported.
    """
    index = defaultdict(list)
    for rank, disc in known.items():
        index[fingerprint(disc)].append(rank)
    votes = Counter(rank - offset - 1 for offset, disc in seen.items() for rank in index.get(fingerprint(disc), ()))
    if not votes:
        return None
    start, best = votes.most_common(1)[0]
    if sum(1 for _, n in votes.items() if n == best) != 1:
        return None
    return start


def wanted(start, offsets, holes, total):
    """Screen offsets, in the given order, whose disc is a hole still to read."""
    return [offset for offset in offsets if 1 <= start + offset + 1 <= total and start + offset + 1 in holes]
