"""
index.py

A hand-rolled in-memory key -> value index.

The assignment explicitly forbids relying on the language's built-in
dictionary/map type for the index, so this is implemented as a plain
array of [key, value] pairs, scanned linearly. This is the "simple
design" the spec calls out as acceptable (O(n) get/set/delete instead
of O(1)), and it is reused for both the top-level database index and
for the nested field storage used by HSET/HGET/HGETALL.

"Last write wins" is enforced by mutating the existing slot in place
when a key already exists, rather than appending a duplicate.
"""


class Index:
    def __init__(self):
        # Each entry is a 2-element list: [key, value]. Using a list
        # (array) instead of a dict is the whole point of this class.
        self._entries = []

    def _find(self, key):
        """Linear scan for the slot index holding `key`, or -1."""
        for i in range(len(self._entries)):
            if self._entries[i][0] == key:
                return i
        return -1

    def set(self, key, value):
        i = self._find(key)
        if i == -1:
            self._entries.append([key, value])
        else:
            self._entries[i][1] = value  # last write wins

    def get(self, key):
        i = self._find(key)
        if i == -1:
            return None
        return self._entries[i][1]

    def exists(self, key):
        return self._find(key) != -1

    def delete(self, key):
        i = self._find(key)
        if i == -1:
            return False
        # remove by shifting (no built-in dict, so no O(1) pop either)
        del self._entries[i]
        return True

    def keys(self):
        return [e[0] for e in self._entries]

    def items(self):
        return [(e[0], e[1]) for e in self._entries]

    def clear(self):
        self._entries = []

    def __len__(self):
        return len(self._entries)
