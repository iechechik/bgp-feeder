"""Применение листов в bird2: configure с автооткатом (timeout) -> проверка счётчиков -> confirm."""
from __future__ import annotations

import logging
import re
import subprocess
import time

log = logging.getLogger("bird")


class Bird:
    def __init__(self, cmd, peer="vyos", configure_timeout=120, settle_seconds=5):
        self.cmd, self.peer = list(cmd), peer
        self.timeout, self.settle = configure_timeout, settle_seconds

    def q(self, command: str) -> str:
        log.debug("$ %s", " ".join(self.cmd + [command]))
        r = subprocess.run(self.cmd + [command], capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        log.debug("rc=%d: %s", r.returncode, out.strip().replace("\n", " | ")[:500])
        if r.returncode != 0:
            raise RuntimeError(f"birdc {command!r}: код {r.returncode}: {out.strip()[:300]}")
        return out

    @staticmethod
    def _count(out: str):
        m = re.search(r"(\d+) of \d+ routes", out)
        return int(m.group(1)) if m else None

    def apply(self, expected: dict[str, tuple[str, int]]):
        """expected: {имя_листа: (community 'A:B', число маршрутов)} -> (ok, [сообщения])"""
        msgs = []
        try:
            out = self.q("configure check")
            if "Configuration OK" not in out:
                return False, [f"configure check: {out.strip()[:300]}"]
            log.info("configure check: OK")
            out = self.q(f"configure timeout {self.timeout}" if self.timeout else "configure")
            log.info("configure применён%s", f" (автооткат через {self.timeout} с)" if self.timeout else "")
            if re.search(r"fail|error|syntax", out, re.I):
                return False, [f"configure: {out.strip()[:300]}"]
            time.sleep(self.settle)
            for name, (comm, want) in expected.items():
                a, b = comm.split(":")
                got_p = self._count(self.q(f"show route protocol {name} count"))
                got_e = self._count(self.q(f"show route export {self.peer} where ({a},{b}) ~ bgp_community count"))
                log.info("проверка %-16s файл %6d  bird %6s  экспорт %6s  %s", name, want, got_p, got_e,
                         "OK" if got_p == want == got_e else "РАСХОЖДЕНИЕ")
                if got_p != want or got_e != want:
                    msgs.append(f"{name}: в файле {want}, в bird {got_p}, в экспорте {got_e}")
            if "Established" not in self.q(f"show protocols {self.peer}"):
                msgs.append(f"BGP-сессия {self.peer} не Established")
            if msgs:
                tail = f"конфиг НЕ подтверждён, bird откатится сам через {self.timeout} с" if self.timeout else "автооткат выключен"
                return False, msgs + [tail]
            if self.timeout:
                out = self.q("configure confirm")
                log.info("configure confirm: %s", out.strip().splitlines()[-1] if out.strip() else "")
                if "confirmed" not in out.lower():
                    return False, [f"configure confirm: {out.strip()[:200]}"]
            return True, []
        except Exception as e:  # noqa: BLE001
            return False, [str(e)]
