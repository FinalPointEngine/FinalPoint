"""ThirdEye's own state (its own Neon database through storage.repository)."""
from __future__ import annotations

import json


class Store:
    def __init__(self, load, save):
        self._load, self._save = load, save
        self.v = self._rd("te_verdicts", {})      # key -> verdict record
        self.meta = self._rd("te_meta", {})

    def _rd(self, k, d):
        try:
            raw = self._load(k)
            return json.loads(raw) if raw else d
        except Exception:
            return d

    def flush(self):
        self._save("te_verdicts", json.dumps(self.v))
        self._save("te_meta", json.dumps(self.meta))
