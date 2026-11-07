"""Список префиксов по URL(ам): строки вида '1.2.3.0/24' или '1.2.3.4' (antifilter, refilter, ...)."""
import logging
from urllib.parse import urlparse

from ..netset import parse_line
from . import ImporterError

log = logging.getLogger("url_list")
NAME = "url_list"
PARAMS = {"urls": list, "min_prefixes": int, "ttl": (int, float)}
REQUIRED = ("urls",)


class Importer:
    def __init__(self, name, params, ctx):
        self.name, self.ctx = name, ctx
        self.urls = params["urls"]
        self.min_prefixes = params.get("min_prefixes", 100)
        self.ttl = params.get("ttl", 3600)

    def run(self):
        out = {}
        for i, url in enumerate(self.urls):
            path = self.ctx.fetch(url, f"{self.name}-{i}.txt", self.ttl)
            host = urlparse(url).netloc or url
            before = len(out)
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    n = parse_line(line)
                    if n is not None:
                        out.setdefault(n, host)
            log.info("%s: %s -> +%d новых префиксов (всего %d)", self.name, host, len(out) - before, len(out))
        if len(out) < self.min_prefixes:
            raise ImporterError(f"получено {len(out)} префиксов, ожидалось не меньше {self.min_prefixes}")
        return out
