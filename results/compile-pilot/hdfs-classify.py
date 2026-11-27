import re

_B = r"blk_-?\d+"
_IP = r"\d+\.\d+\.\d+\.\d+"

_RULES = [
    ("E1", re.compile(r"^/?" + _IP + r":\d+ Served block " + _B + r" to /" + _IP + r"\s*$")),
    ("E2", re.compile(r"^" + _IP + r":\d+ Starting thread to transfer block " + _B + r" to " + _IP + r":\d+(\s+" + _IP + r":\d+)*\s*$")),
    ("E3", re.compile(r"^/?" + _IP + r":\d+:Got exception while serving " + _B + r" to /" + _IP + r":\s*$")),
    ("E4", re.compile(r"^BLOCK\* ask " + _IP + r":\d+ to delete\s+" + _B + r"(\s+" + _B + r")*\s*$")),
    ("E5", re.compile(r"^BLOCK\* ask " + _IP + r":\d+ to replicate " + _B + r" to datanode\(s\)\s+" + _IP + r":\d+(\s+" + _IP + r":\d+)*\s*$")),
    ("E6", re.compile(r"^BLOCK\* NameSystem\.addStoredBlock: blockMap updated: " + _IP + r":\d+ is added to " + _B + r" size \d+\s*$")),
    ("E7", re.compile(r"^BLOCK\* NameSystem\.allocateBlock: /\S*/part-\d+\. " + _B + r"\s*$")),
    ("E8", re.compile(r"^BLOCK\* NameSystem\.delete: " + _B + r" is added to invalidSet of " + _IP + r":\d+\s*$")),
    ("E9", re.compile(r"^Deleting block " + _B + r" file /\S*/" + _B + r"\s*$")),
    ("E10", re.compile(r"^PacketResponder \d+ for block " + _B + r" terminating\s*$")),
    ("E11", re.compile(r"^Received block " + _B + r" of size \d+ from /" + _IP + r"\s*$")),
    ("E12", re.compile(r"^Received block " + _B + r" src: /" + _IP + r":\d+ dest: /" + _IP + r":\d+ of size \d+\s*$")),
    ("E13", re.compile(r"^Receiving block " + _B + r" src: /" + _IP + r":\d+ dest: /" + _IP + r":\d+\s*$")),
    ("E14", re.compile(r"^Verification succeeded for " + _B + r"\s*$")),
]


def classify(text):
    if not isinstance(text, str):
        return None
    t = text.strip()
    hits = [eid for eid, rx in _RULES if rx.match(t)]
    if len(hits) == 1:
        return hits[0]
    return None
