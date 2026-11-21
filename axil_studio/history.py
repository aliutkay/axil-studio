"""Undo / redo: a list of snapshots (plain data) and a position in it.

The owner pushes a snapshot after every change; undo / redo give back the snapshot to restore.
Used for the project (ProjectContext) and for the register map being edited in the Register Map
Editor, which is kept apart from the project until it is saved."""

import json


class History:
    def __init__(self, limit=200):
        self.limit = limit
        self.states, self.pos, self.saved = [], -1, -1

    @staticmethod
    def _key(state):
        return json.dumps(state, sort_keys=True, ensure_ascii=False)

    def reset(self, state, saved=True):
        """Start again from `state` (a project / register map was opened or created)."""
        self.states, self.pos = [self._key(state)], 0
        self.saved = 0 if saved else -1

    def push(self, state):
        """Record the state after a change; returns False when nothing changed."""
        key = self._key(state)
        if self.pos >= 0 and self.states[self.pos] == key:
            return False
        del self.states[self.pos + 1:]
        if self.saved > self.pos:
            self.saved = -1                       # the saved state can no longer be reached
        self.states.append(key)
        self.pos += 1
        if len(self.states) > self.limit:
            drop = len(self.states) - self.limit
            self.states = self.states[drop:]
            self.pos -= drop
            self.saved = self.saved - drop if self.saved >= drop else -1
        return True

    def can_undo(self):
        return self.pos > 0

    def can_redo(self):
        return 0 <= self.pos < len(self.states) - 1

    def undo(self):
        if not self.can_undo():
            return None
        self.pos -= 1
        return json.loads(self.states[self.pos])

    def redo(self):
        if not self.can_redo():
            return None
        self.pos += 1
        return json.loads(self.states[self.pos])

    def mark_saved(self):
        self.saved = self.pos

    def is_saved(self):
        return self.pos == self.saved
