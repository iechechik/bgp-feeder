"""Ручные списки из файлов (принимает и старый формат 'route X blackhole; # коммент')."""
import logging
import os

from ..netset import parse_line

log = logging.getLogger("manual")
NAME = "manual"
PARAMS = {"files": list}
REQUIRED = ("files",)


class Importer:
    def __init__(self, name, params, ctx):
        self.ctx = ctx
        self.files = params["files"]

    def run(self):
        out = {}
        for rel in self.files:
            path = os.path.join(self.ctx.base, rel)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    n = parse_line(line)
                    if n is not None:
                        note = line.split("#", 1)[1].strip() if "#" in line else ""
                        out.setdefault(n, note or os.path.basename(rel))
            log.info("%s: %d префиксов накопительно", rel, len(out))
        return out
