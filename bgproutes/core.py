"""Конфиг (со строгой проверкой ключей), прогон импортёров, разрешение конфликтов по приоритету, запись результата."""
from __future__ import annotations

import difflib
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime

from .fetch import fetch
from .importers import registry
from .netset import Cover, collapse, parse_line, round_up

log = logging.getLogger("core")
NUM = (int, float)
GLOBAL_SCHEMA = {"out_dir": str, "state_dir": str, "bird_include": str, "user_agent": str,
                 "max_age_hours": NUM, "max_routes": int, "max_change_pct": NUM, "notify_cmd": str}
BIRD_SCHEMA = {"birdc": list, "peer": str, "configure_timeout": int, "settle_seconds": NUM, "reload": bool}
LIST_SCHEMA = {"name": str, "importer": str, "priority": int, "community": str, "comment": str,
               "authoritative": bool, "max_len": int, "enabled": bool}
LIST_REQUIRED = ("name", "importer", "priority", "community")
TOP_SECTIONS = {"global", "bird", "list"}


class Ctx:
    def __init__(self, base: str, g: dict):
        self.base = base
        self.state = os.path.join(base, g.get("state_dir", "state"))
        self.out = os.path.join(base, g.get("out_dir", "out"))
        self.ua = g.get("user_agent", "bgp-routes/1.0")
        self.max_age = g.get("max_age_hours", 48) * 3600
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def warn(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def fetch(self, url, name, ttl):
        return fetch(url, os.path.join(self.state, "dl", name), ttl, self.ua, self.max_age, warn=self.warn)


@dataclass
class ListResult:
    name: str
    status: str = "ok"          # ok | failed | drift
    kept: list = field(default_factory=list)
    raw: int = 0
    dropped: int = 0
    prev: int | None = None
    msg: str = ""


# ---------------------------------------------------------------- конфиг
def _tname(t):
    return "/".join(x.__name__ for x in (t if isinstance(t, tuple) else (t,)))


def check_table(label, tbl, schema, errors, required=()):
    for k in required:
        if k not in tbl:
            errors.append(f"{label}: нет обязательного ключа {k!r}")
    for k, v in tbl.items():
        if k not in schema:
            hint = difflib.get_close_matches(k, list(schema), n=1)
            errors.append(f"{label}: неизвестный ключ {k!r}" + (f" (возможно, {hint[0]!r})" if hint else ""))
            continue
        types = schema[k] if isinstance(schema[k], tuple) else (schema[k],)
        if not isinstance(v, types) or (isinstance(v, bool) and bool not in types):
            errors.append(f"{label}: {k} должен быть {_tname(schema[k])}, а не {type(v).__name__}")


def validate_config(cfg, base):
    errors = []
    for k in cfg:
        if k not in TOP_SECTIONS:
            hint = difflib.get_close_matches(k, list(TOP_SECTIONS), n=1)
            errors.append(f"конфиг: неизвестная секция {k!r}" + (f" (возможно, {hint[0]!r})" if hint else ""))
    check_table("[global]", cfg.get("global", {}), GLOBAL_SCHEMA, errors)
    check_table("[bird]", cfg.get("bird", {}), BIRD_SCHEMA, errors)
    reg = registry()
    names, comms = set(), {}
    for i, l in enumerate(cfg.get("list", [])):
        label = f"[[list]] {l.get('name', f'#{i + 1}')}"
        imp = reg.get(l.get("importer"))
        if l.get("importer") is not None and imp is None:
            errors.append(f"{label}: нет импортёра {l['importer']!r} (есть: {', '.join(sorted(reg))})")
        schema = dict(LIST_SCHEMA, **(imp.PARAMS if imp else {}))
        check_table(label, l, schema, errors, LIST_REQUIRED + (tuple(imp.REQUIRED) if imp else ()))
        n = l.get("name")
        if isinstance(n, str):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n):
                errors.append(f"{label}: имя должно быть вида [A-Za-z_][A-Za-z0-9_]* (так назовётся протокол bird)")
            if n in names:
                errors.append(f"{label}: повторяющееся имя листа")
            names.add(n)
        c = l.get("community")
        if isinstance(c, str):
            if not re.fullmatch(r"\d{1,5}:\d{1,5}", c):
                errors.append(f"{label}: community должен быть вида 65002:6")
            elif l.get("enabled", True):
                if c in comms:
                    errors.append(f"{label}: community {c} уже занят листом {comms[c]}")
                comms[c] = l.get("name")
        ml = l.get("max_len")
        if isinstance(ml, int) and not isinstance(ml, bool) and not 8 <= ml <= 32:
            errors.append(f"{label}: max_len должен быть в диапазоне 8..32")
    if errors:
        raise SystemExit("Ошибки конфигурации:\n  " + "\n  ".join(errors))
    # конструкторы импортёров без сетевого ввода-вывода: ловим, например, битые regex
    ctx = Ctx(base, cfg.get("global", {}))
    for l in cfg.get("list", []):
        try:
            make_importer(l, ctx)
        except Exception as e:  # noqa: BLE001
            errors.append(f"[[list]] {l['name']}: {type(e).__name__}: {e}")
    if errors:
        raise SystemExit("Ошибки конфигурации:\n  " + "\n  ".join(errors))


def load_config(path: str):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("global", {})
    base = os.path.dirname(os.path.abspath(path))
    validate_config(cfg, base)
    return cfg, base


def out_path(ctx, name):
    return os.path.join(ctx.out, f"{name}.txt")


def read_out(path):
    nets = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.startswith("route "):
                    n = parse_line(line)
                    if n:
                        nets.append(n)
    return nets


def make_importer(l, ctx):
    imp = registry()[l["importer"]]
    params = {k: v for k, v in l.items() if k in imp.PARAMS}
    return imp(l["name"], params, ctx)


# ---------------------------------------------------------------- разрешение
def resolve(cfg, ctx: Ctx, force=False, only=None):
    g = cfg["global"]
    max_change = g.get("max_change_pct", 30)
    lists = [l for l in cfg["list"] if l.get("enabled", True)]
    lists.sort(key=lambda l: (-l["priority"], l["name"]))
    results: dict[str, ListResult] = {}
    auth_nets: list = []     # сети вышестоящих авторитетных списков
    all_nets: set = set()    # все сети вышестоящих списков
    os.makedirs(os.path.join(ctx.state, "raw"), exist_ok=True)
    for l in lists:
        name = l["name"]
        r = ListResult(name)
        results[name] = r
        prev = read_out(out_path(ctx, name))
        r.prev = len(prev) if prev else None
        skipped = bool(only and name not in only)
        try:
            if skipped:
                raise RuntimeError("пропущен (--only)")
            log.info("== %s (importer=%s, priority=%d%s)", name, l["importer"], l["priority"],
                     ", authoritative" if l.get("authoritative") else "")
            raw = make_importer(l, ctx).run()
            with open(os.path.join(ctx.state, "raw", f"{name}.txt"), "w", encoding="utf-8") as f:
                for n in sorted(raw):
                    f.write(f"{n}\t{raw[n]}\n")
            r.raw = len(raw)
            nets = set(raw)
            if l.get("max_len"):
                nets = round_up(nets, l["max_len"])
            nets = collapse(nets)                       # lossless-склейка до вычитания
            auth = Cover(auth_nets)
            # выкидываем: точный дубль префикса вышестоящего списка | всё, что покрыто вышестоящим авторитетным списком
            kept = [n for n in nets if not (n in all_nets or auth.covers(n))]
            r.dropped = len(nets) - len(kept)
            r.kept = sorted(kept)
            log.info("%s: источник %d -> после max_len/склейки %d -> отброшено вышестоящими %d -> итог %d",
                     name, len(raw), len(nets), r.dropped, len(r.kept))
            if (r.prev or 0) >= 100 and not force:
                delta = abs(len(r.kept) - r.prev) * 100 / r.prev
                if delta > max_change:
                    r.status = "drift"
                    r.msg = f"размер изменился {r.prev} -> {len(r.kept)} ({delta:.0f}% > {max_change}%), оставлен прежний"
                    ctx.error(f"{name}: {r.msg}")
        except Exception as e:  # noqa: BLE001
            r.status = "failed"
            r.msg = str(e)
            if not skipped:
                ctx.error(f"{name}: {e}")
        if r.status != "ok":
            log.info("%s: статус %s (%s), используется прежний результат (%d маршрутов)", name, r.status, r.msg, len(prev))
            r.kept = prev  # для вычитания из нижестоящих используем прежний результат
        if l.get("authoritative"):
            auth_nets += r.kept
        all_nets.update(r.kept)
    return lists, results


def write_outputs(cfg, ctx: Ctx, lists, results):
    """Атомарно: все файлы во временные, затем replace."""
    g = cfg["global"]
    os.makedirs(ctx.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pending = []
    for l in lists:
        r = results[l["name"]]
        if r.status != "ok":
            continue
        tmp = out_path(ctx, l["name"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"# bgp-routes: {l['name']}\n# generated: {stamp}\n")
            f.write(f"# community: {l['community']}  priority: {l['priority']}\n")
            if l.get("comment"):
                f.write(f"# comment: {l['comment']}\n")
            f.write(f"# routes: {len(r.kept)} (raw {r.raw}, dropped by higher priority: {r.dropped})\n\n")
            for n in r.kept:
                f.write(f"route {n} blackhole;\n")
        pending.append((tmp, out_path(ctx, l["name"])))
        log.info("записан %s (%d маршрутов)", out_path(ctx, l["name"]), len(r.kept))
    # protocols.conf генерируется из конфига
    inc = g.get("bird_include", "/etc/bird/bgp-routes/out")
    conf_tmp = os.path.join(ctx.out, "protocols.conf.tmp")
    with open(conf_tmp, "w", encoding="utf-8") as f:
        f.write("# generated by bgp-routes - do not edit\n\n")
        for l in lists:
            if l.get("comment"):
                f.write(f"# {l['name']}: {l['comment']}\n")
            f.write(f"protocol static {l['name']} {{\n  ipv4 {{ preference {l['priority']}; }};\n"
                    f"  include \"{inc}/{l['name']}.txt\";\n}}\n")
        f.write("\nfilter bgp_routes_export {\n")
        for l in lists:
            a, b = l["community"].split(":")
            f.write(f"  if proto = \"{l['name']}\" then {{ bgp_community.add(({a}, {b})); accept; }}\n")
        f.write("  reject;\n}\n")
    pending.append((conf_tmp, os.path.join(ctx.out, "protocols.conf")))
    for tmp, dst in pending:
        os.replace(tmp, dst)
