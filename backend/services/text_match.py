"""One tokenizer for both matching and claim checking.

Everything that decides "does this text mention that skill" goes through here.

Skills arrive in prose (`Built services in Python.`), in comma-separated lists with or
without spaces (`Java,C/C++,Python`), and hyphenated (`React-based`, `machine-learning`).
Tokens therefore split on whitespace *and* on list separators, drop punctuation at the
edges, and keep the punctuation that carries meaning: .NET, C++, C#, node.js, ci/cd,
scikit-learn.

Compounds are read both ways — whole and split — so `ci/cd` matches itself while
`machine-learning` still matches the phrase "machine learning".
"""

import re

from services.match import normalize_skill

# always a separator, never part of a skill name
SEPARATORS = re.compile(r"[\s,;|•·]+")
EDGE_QUOTES = "\"'`‘’“”()[]{}<>«»"
EDGE_TRAILING = ".,;:!?"
SPLIT_INSIDE = re.compile(r"[-/]")
CURLY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _clean(raw):
    raw = raw.strip(EDGE_QUOTES)
    raw = raw.rstrip(EDGE_TRAILING)          # "python." → "python", ".net" keeps its dot
    raw = raw.strip(EDGE_QUOTES)
    return raw.removesuffix("'s")


def index(text):
    """Two token sequences (compounds whole and split) plus a set for single words.

    `origin` maps each split piece back to the whole token it came from, so a caller doing
    longest-match can claim a phrase in one coordinate system. Without it, "object-oriented
    programming" claims the phrase in `whole` while "programming" is still free to match the
    same words in `split`.
    """
    if not text:
        return {"whole": [], "split": [], "terms": set(), "origin": []}

    whole, split, origin, terms = [], [], [], set()
    for raw in SEPARATORS.split(text.lower().translate(CURLY)):
        token = _clean(raw)
        if not token:
            continue

        whole.append(token)
        terms.add(token)
        terms.add(normalize_skill(token))     # node.js also answers to node

        pieces = [_clean(piece) for piece in SPLIT_INSIDE.split(token)]
        pieces = [piece for piece in pieces if piece]
        if len(pieces) > 1:
            split.extend(pieces)              # machine-learning → machine, learning
            origin.extend([len(whole) - 1] * len(pieces))
            for piece in pieces:
                terms.add(piece)
                terms.add(normalize_skill(piece))
        else:
            split.append(token)
            origin.append(len(whole) - 1)

    return {"whole": whole, "split": split, "terms": terms, "origin": origin}


def _wanted(term):
    return [t for t in (_clean(w) for w in SEPARATORS.split(term.lower().translate(CURLY))) if t]


def covered_tokens(indexed, term):
    """Which whole-token positions this term occupies, or an empty set if it isn't there.

    Everything is reported in `whole` coordinates — a split match reports the compound token
    it came out of — so a caller can tell that "programming" and "object-oriented
    programming" are competing for the same words.
    """
    wanted = _wanted(term)
    if not wanted:
        return set()

    covered = set()
    span = len(wanted)

    for i, token in enumerate(indexed["whole"]):
        if span == 1 and (token == wanted[0] or normalize_skill(token) == normalize_skill(wanted[0])):
            covered.add(i)
    if span > 1:
        for i in range(len(indexed["whole"]) - span + 1):
            if indexed["whole"][i:i + span] == wanted:
                covered.update(range(i, i + span))

    origin = indexed["origin"]
    pieces = indexed["split"]
    for i in range(len(pieces) - span + 1):
        if pieces[i:i + span] == wanted:
            covered.update(origin[i:i + span])
    if span == 1:
        for i, piece in enumerate(pieces):
            if normalize_skill(piece) == normalize_skill(wanted[0]):
                covered.add(origin[i])

    return covered


def mentions(indexed, term):
    """Whole-token match. Multi-word terms must appear as consecutive tokens."""
    wanted = [t for t in (_clean(w) for w in SEPARATORS.split(term.lower().translate(CURLY))) if t]
    if not wanted:
        return False

    if len(wanted) == 1:
        return wanted[0] in indexed["terms"] or normalize_skill(wanted[0]) in indexed["terms"]

    span = len(wanted)
    return any(
        sequence[i:i + span] == wanted
        for sequence in (indexed["whole"], indexed["split"])
        for i in range(len(sequence) - span + 1)
    )
