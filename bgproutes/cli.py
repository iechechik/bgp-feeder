from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys

from . import core
from .bird import Bird
from .netset import parse_line
from .notify import notify


def make_bird(cfg):
    b = cfg.get("bird", {})
    return Bird(b.get("birdc", ["docker", "exec", "bird2", "birdc"]), b.get("peer", "vyos"),
                b.get("configure_timeout", 120), b.get("settle_seconds", 5))


def expected_from_out(cfg, ctx):
    """Ожидаемое число маршрутов по уже записанным out/*.txt."""
    return {l["name"]: (l["community"], len(core.read_out(core.out_path(ctx, l["name"]))))
            for l in cfg["list"] if l.get("enabled", True)}


def do_reload(cfg, ctx):
    ok, msgs = make_bird(cfg).apply(expected_from_out(cfg, ctx))
    for m in msgs:
        ctx.error(f"bird: {m}")
    print("bird: конфиг применён и подтверждён" if ok else "bird: ПРИМЕНЕНИЕ НЕ УДАЛОСЬ", file=sys.stderr if not ok else sys.stdout)
    return ok


def finish(cfg, ctx, args):
    for w in ctx.warnings:
        print(f"WARN: {w}", file=sys.stderr)
    for e in ctx.errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if not args.no_notify and cfg["global"].get("notify_cmd"):
        notify(cfg["global"]["notify_cmd"], os.path.join(ctx.state, "notify.json"),
               ctx.errors + ctx.warnings, lambda m: print(m, file=sys.stderr))


def cmd_run(cfg, base, args):
    ctx = core.Ctx(base, cfg["global"])
    lists, results = core.resolve(cfg, ctx, force=args.force, only=set(args.only or []))
    total = sum(len(r.kept) for r in results.values())
    max_routes = cfg["global"].get("max_routes")
    fatal = False
    if max_routes and total > max_routes:
        ctx.error(f"всего маршрутов {total} > лимита {max_routes}, листы не обновлены")
        fatal = True
    print(f"{'list':16} {'status':7} {'routes':>7} {'raw':>7} {'dropped':>8} {'prev':>7}")
    for l in lists:
        r = results[l["name"]]
        print(f"{r.name:16} {r.status:7} {len(r.kept):7} {r.raw:7} {r.dropped:8} {r.prev if r.prev is not None else '-':>7}  {r.msg}")
    print(f"{'TOTAL':16} {'':7} {total:7}")
    if args.dry_run:
        finish(cfg, ctx, argparse.Namespace(no_notify=True))
        return 1 if fatal else (2 if ctx.errors else 0)
    if not fatal:
        core.write_outputs(cfg, ctx, lists, results)
        reload = cfg.get("bird", {}).get("reload", False) if args.reload is None else args.reload
        if reload:
            do_reload(cfg, ctx)
    finish(cfg, ctx, args)
    return 1 if fatal else (2 if ctx.errors else 0)


def cmd_reload(cfg, base, args):
    ctx = core.Ctx(base, cfg["global"])
    ok = do_reload(cfg, ctx)
    finish(cfg, ctx, args)
    return 0 if ok else 2


def cmd_check(cfg, base, args):
    print(f"конфиг OK: {len(cfg['list'])} листов ({sum(1 for l in cfg['list'] if l.get('enabled', True))} включено)")
    return 0


def cmd_explain(cfg, base, args):
    ctx = core.Ctx(base, cfg["global"])
    ip = ipaddress.ip_address(args.ip)
    best = None
    print(f"== {ip}: что лежит в итоговых листах")
    for l in cfg["list"]:
        for n in core.read_out(core.out_path(ctx, l["name"])):
            if ip in n:
                print(f"  {l['name']:16} {str(n):20} community {l['community']}  {l.get('comment', '')}")
                if best is None or n.prefixlen > best[0].prefixlen:
                    best = (n, l)
    if best:
        print(f"РЕШЕНИЕ: {best[1]['name']} / {best[1]['community']} (самый длинный префикс {best[0]})  {best[1].get('comment', '')}")
    else:
        print("РЕШЕНИЕ: нет совпадений -> маршрут по умолчанию")
    print("== в сырых данных источников (до приоритетов)")
    for l in cfg["list"]:
        p = os.path.join(ctx.state, "raw", f"{l['name']}.txt")
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            pfx, _, note = line.rstrip("\n").partition("\t")
            n = parse_line(pfx)
            if n and ip in n:
                print(f"  {l['name']:16} {pfx:20} {note}")
    return 0


def cmd_report(cfg, base, args):
    ctx = core.Ctx(base, cfg["global"])
    l = next((x for x in cfg["list"] if x["name"] == args.list), None)
    if not l:
        raise SystemExit(f"нет листа {args.list}")
    imp = core.make_importer(l, ctx)
    imp.run()
    for w in ctx.warnings:
        print(f"WARN: {w}", file=sys.stderr)
    print(f"{'правило':40} {'AS':>5} {'префиксов':>10}  примеры")
    for rule, n_as, n_pfx, sample in getattr(imp, "stats", []):
        print(f"{rule:40} {n_as:5} {n_pfx:10}  {'; '.join(s[:30] for s in sample)}")
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("-v", "--verbose", action="count", help="подробный вывод (-v шаги, -vv + команды birdc)")
    ap = argparse.ArgumentParser(prog="bgp-routes", parents=[common])
    ap.add_argument("-c", "--config", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.toml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common], help="собрать листы (и применить в bird с --reload)")
    r.add_argument("--dry-run", action="store_true", help="посчитать и показать, ничего не писать")
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--force", action="store_true", help="игнорировать защиту от дрейфа размера")
    r.add_argument("--only", nargs="*", metavar="LIST", help="обновить только эти листы")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--reload", dest="reload", action="store_true", default=None, help="после записи применить в bird")
    g.add_argument("--no-reload", dest="reload", action="store_false")
    rl = sub.add_parser("reload", parents=[common], help="применить уже записанные out/*.txt в bird (configure с автооткатом и проверкой)")
    rl.add_argument("--no-notify", action="store_true")
    sub.add_parser("check", parents=[common], help="проверить config.toml")
    e = sub.add_parser("explain", parents=[common], help="почему IP попал в тот или иной лист")
    e.add_argument("ip")
    p = sub.add_parser("report", parents=[common], help="разбор правил as_select (сколько AS/префиксов даёт каждое правило)")
    p.add_argument("list")
    args = ap.parse_args(argv)
    v = getattr(args, "verbose", 0)
    logging.basicConfig(level=logging.DEBUG if v >= 2 else logging.INFO if v == 1 else logging.WARNING,
                        format="%(asctime)s %(name)-9s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    cfg, base = core.load_config(args.config)
    return {"run": cmd_run, "reload": cmd_reload, "check": cmd_check, "explain": cmd_explain, "report": cmd_report}[args.cmd](cfg, base, args)


if __name__ == "__main__":
    sys.exit(main())
