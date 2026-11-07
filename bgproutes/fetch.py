"""Скачивание с кэшем: атомарная запись, при сбое - старый кэш не старше max_age."""
from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.request
from datetime import datetime

log = logging.getLogger("fetch")


class FetchError(Exception):
    pass


def fetch(url: str, dest: str, ttl: float, ua: str, max_age: float, warn=None, timeout: int = 60) -> str:
    def age():
        return time.time() - os.path.getmtime(dest)

    name = os.path.basename(dest)
    if os.path.exists(dest) and age() < ttl:
        log.info("%s: кэш свежий (%.0f мин < ttl %.0f мин), скачивание пропущено", name, age() / 60, ttl / 60)
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    t0 = time.time()
    log.info("%s: скачиваю %s", name, url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        if os.path.getsize(tmp) == 0:
            raise FetchError("пустой ответ")
        size = os.path.getsize(tmp)
        os.replace(tmp, dest)
        log.info("%s: получено %d байт за %.1f с", name, size, time.time() - t0)
        return dest
    except Exception as e:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if os.path.exists(dest) and age() <= max_age:
            if warn:
                when = datetime.fromtimestamp(os.path.getmtime(dest)).strftime("%Y-%m-%d %H:%M")
                warn(f"{url}: {e}; использован кэш от {when}")
            return dest
        raise FetchError(f"{url}: {e}") from e
