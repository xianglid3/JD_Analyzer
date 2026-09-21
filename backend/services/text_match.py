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


# Phrases that mean something other than their head noun, so their pieces must not be read as
# skills on their own. A LiDAR bullet says "point-cloud filtering" and used to match an AWS
# requirement, because splitting the compound put "cloud" in `terms`.
#
# Deliberately a list of *compounds*, not of banned words: "cloud" is a real skill and still
# matches wherever it stands on its own. Each entry claims only the occurrence it covers — a
# bullet mentioning point clouds AND cloud infrastructure still matches cloud.
COMPOUND_TERMS = frozenset({
    "point cloud",
    "end to end",
    "real time",
    "state of the art",
})


def _compound_spans(whole):
    """Whole-token positions covered by a protected compound, in this text.

    Handles both spellings in one pass: "point cloud" as two tokens, and "point-cloud" as one
    hyphenated token whose pieces would otherwise leak.
    """
    claimed = set()
    for compound in COMPOUND_TERMS:
        wanted = compound.split()
        span = len(wanted)
        for i, token in enumerate(whole):
            # the hyphenated spelling: one token that splits into exactly this phrase
            if [p for p in (_clean(x) for x in SPLIT_INSIDE.split(token)) if p] == wanted:
                claimed.add(i)
            # the spaced spelling: consecutive tokens
            if i + span <= len(whole) and [
                normalize_skill(t) for t in whole[i:i + span]
            ] == [normalize_skill(w) for w in wanted]:
                claimed.update(range(i, i + span))
    return claimed


def index(text):
    """Two token sequences (compounds whole and split) plus a set for single words.

    `origin` maps each split piece back to the whole token it came from, so a caller doing
    longest-match can claim a phrase in one coordinate system. Without it, "object-oriented
    programming" claims the phrase in `whole` while "programming" is still free to match the
    same words in `split`.

    `claimed` holds the positions covered by a `COMPOUND_TERMS` phrase. Those positions still
    appear in `whole` and `split` — callers doing their own longest-match need to see them —
    but they contribute nothing to `terms`, which is the set a single-word lookup reads.
    """
    if not text:
        return {"whole": [], "split": [], "terms": set(), "origin": [], "claimed": set()}

    whole, split, origin = [], [], []
    for raw in SEPARATORS.split(text.lower().translate(CURLY)):
        token = _clean(raw)
        if not token:
            continue

        whole.append(token)
        pieces = [_clean(piece) for piece in SPLIT_INSIDE.split(token)]
        pieces = [piece for piece in pieces if piece]
        if len(pieces) > 1:
            split.extend(pieces)              # machine-learning → machine, learning
            origin.extend([len(whole) - 1] * len(pieces))
        else:
            split.append(token)
            origin.append(len(whole) - 1)

    # built after tokenizing, because a compound is recognised across tokens
    claimed = _compound_spans(whole)

    terms = set()
    for position, token in enumerate(whole):
        if position in claimed:
            continue
        terms.add(token)
        terms.add(normalize_skill(token))     # node.js also answers to node
    for piece, source in zip(split, origin):
        if source in claimed:
            continue
        terms.add(piece)
        terms.add(normalize_skill(piece))

    return {"whole": whole, "split": split, "terms": terms, "origin": origin, "claimed": claimed}


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

    # A protected compound owns its own words. "cloud" may not claim the positions that
    # "point cloud" covers — but only those positions, so another "cloud" in the same text is
    # untouched. The compound itself is exempt, or it could not match its own tokens.
    if " ".join(wanted) not in COMPOUND_TERMS:
        covered -= indexed.get("claimed", set())

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
