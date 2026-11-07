"""Работа с наборами IPv4-префиксов."""
from __future__ import annotations

import bisect
import ipaddress
import re
from ipaddress import IPv4Network

_RX = re.compile(r"^\s*(?:route\s+)?(\d{1,3}(?:\.\d{1,3}){3})(?:/(\d{1,2}))?")


def parse_line(line: str) -> IPv4Network | None:
    """'1.2.3.0/24', '1.2.3.4', 'route 1.2.3.0/24 blackhole; # x' -> сеть. Комментарии и мусор -> None."""
    if line.lstrip().startswith("#"):
        return None
    m = _RX.match(line)
    if not m:
        return None
    try:
        return IPv4Network(f"{m.group(1)}/{m.group(2) or 32}", strict=False)
    except ValueError:
        return None


def round_up(nets, max_len: int):
    """Префиксы длиннее max_len округляются до родительской сети /max_len (lossy)."""
    out = set()
    for n in nets:
        out.add(n.supernet(new_prefix=max_len) if n.prefixlen > max_len else n)
    return out


def collapse(nets) -> list[IPv4Network]:
    """Lossless-склейка (соседние -> крупнее, вложенные поглощаются)."""
    return list(ipaddress.collapse_addresses(nets))


class Cover:
    """Быстрая проверка 'сеть целиком внутри одной из сетей набора'."""

    def __init__(self, nets=()):
        merged = sorted(collapse(nets))
        self.starts = [int(n.network_address) for n in merged]
        self.ends = [int(n.broadcast_address) for n in merged]

    def covers(self, net: IPv4Network) -> bool:
        i = bisect.bisect_right(self.starts, int(net.network_address)) - 1
        return i >= 0 and self.ends[i] >= int(net.broadcast_address)
