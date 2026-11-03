"""Natural ordering for names: "2 Fast 2 Furious" before "10,000 B.C.".

SQLite's built-in collations compare byte by byte, so a shelf reads

    1, 1 Giant Leap, 10 000 Hz Legend, 10,000 B.C., 12 Bar Bruise, 12 Monkeys,
    127 Hours, 13, ... , 2 Fast 2 Furious, 2001, 28 Days Later, 300

- "10,000" before "127" before "2", which is neither alphabetical nor
numerical. This is the collation a shelf wants: runs of digits compare by
value, everything else compares case- and accent-blind, and numbers come
before letters (the Unicode default, and what Finder and Explorer do).

A collation is a property of a *connection*: `create_collation` has to be
called on every one, and a query naming a collation the connection does not
know fails with "no such collation sequence: NATSORT". `server.connect()`
registers it for the request connections, and the query builders that can be
handed a bare connection register it themselves.
"""

import re
import unicodedata

_NAME = "NATSORT"
_RUN = re.compile(r"(\d+)")


def _fold(text):
    """Case- and accent-blind form, so "Björk" files with the Bs."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _pieces(text):
    """Number runs become (0, value, digits), other runs (1, folded, 0).

    The kind comes first so a number always sorts before a word, and tuple
    comparison never reaches an int against a str.
    """
    out = []
    for part in _RUN.split(text or ""):
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part), len(part)))
        else:
            out.append((1, _fold(part), 0))
    return out


def compare(left, right):
    """-1, 0 or 1, as `create_collation` expects."""
    a, b = _pieces(left), _pieces(right)
    for one, other in zip(a, b):
        if one != other:
            return -1 if one < other else 1
    return (len(a) > len(b)) - (len(a) < len(b))


def register(con):
    """Teach one connection `NATSORT`; returns the connection."""
    con.create_collation(_NAME, compare)
    return con
