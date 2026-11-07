"""Выбор AS из дампов bgp.tools: по стране, номеру или имени (regex) -> все IPv4-префиксы этих AS.

Параметры: country, exclude_country, asn, names, exclude_names, min_asns, min_prefixes.
Приоритет правил: явный asn > (country | names) без exclude_*.
"""
import csv
import ipaddress
import logging
import re

from . import ImporterError

log = logging.getLogger("as_select")
NAME = "as_select"
PARAMS = {"country": list, "exclude_country": list, "asn": list, "names": list, "exclude_names": list,
          "min_asns": int, "min_prefixes": int}
REQUIRED = ()
ASN_URL = "https://bgp.tools/asns.csv"
TABLE_URL = "https://bgp.tools/table.txt"


class Importer:
    def __init__(self, name, params, ctx):
        self.name, self.ctx = name, ctx
        self.country = {c.upper() for c in params.get("country", [])}
        self.exclude_country = {c.upper() for c in params.get("exclude_country", [])}
        self.asn = {int(a) for a in params.get("asn", [])}
        self.names = [(s, re.compile(s, re.I)) for s in params.get("names", [])]
        self.exclude_names = [re.compile(s, re.I) for s in params.get("exclude_names", [])]
        self.min_asns = params.get("min_asns", 1)
        self.min_prefixes = params.get("min_prefixes", 1)
        self.stats = []  # [(правило, ASN, префиксов, примеры)]

    def _select(self):
        asns_path = self.ctx.fetch(ASN_URL, "bgp.tools-asns.csv", 86400)
        chosen, rule_of = {}, {}
        with open(asns_path, encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            if not {"asn", "name", "cc"} <= set(rd.fieldnames or []):
                raise ImporterError(f"неожиданный заголовок asns.csv: {rd.fieldnames}")
            for row in rd:
                try:
                    asn = int(row["asn"].upper().removeprefix("AS"))
                except ValueError:
                    continue
                name, cc = row["name"], (row["cc"] or "").upper()
                rule = None
                if asn in self.asn:
                    rule = f"asn:{asn}"
                else:
                    if cc in self.country:
                        rule = f"country:{cc}"
                    else:
                        for label, rx in self.names:
                            if rx.search(name):
                                rule = f"name:{label}"
                                break
                    if rule and (cc in self.exclude_country or any(rx.search(name) for rx in self.exclude_names)):
                        rule = None
                if rule:
                    chosen[str(asn)] = name
                    rule_of[str(asn)] = rule
        return chosen, rule_of

    def run(self):
        chosen, rule_of = self._select()
        if len(chosen) < self.min_asns:
            raise ImporterError(f"выбрано {len(chosen)} AS, ожидалось не меньше {self.min_asns}")
        log.info("%s: выбрано %d AS по правилам", self.name, len(chosen))
        table_path = self.ctx.fetch(TABLE_URL, "bgp.tools-table.txt", 14400)
        out, per_rule = {}, {}
        with open(table_path, encoding="utf-8") as f:
            for line in f:
                p = line.split()
                if len(p) < 2 or p[1] not in chosen or ":" in p[0]:
                    continue
                try:
                    net = ipaddress.IPv4Network(p[0], strict=False)
                except ValueError:
                    continue
                if net not in out:
                    out[net] = f"AS{p[1]} {chosen[p[1]]}"
                    per_rule.setdefault(rule_of[p[1]], set()).add(net)
        log.info("%s: найдено %d IPv4-префиксов", self.name, len(out))
        if len(out) < self.min_prefixes:
            raise ImporterError(f"получено {len(out)} префиксов, ожидалось не меньше {self.min_prefixes}")
        by_rule_as = {}
        for a, r in rule_of.items():
            by_rule_as.setdefault(r, []).append(chosen[a])
        self.stats = sorted(
            ((r, len(v), len(per_rule.get(r, ())), v[:3]) for r, v in by_rule_as.items()),
            key=lambda x: -x[2],
        )
        return out
