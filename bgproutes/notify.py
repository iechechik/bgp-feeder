"""Уведомления: шлём, только когда набор проблем изменился или прошло 24 часа (чтобы не спамить каждые 12ч)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

REPEAT = 24 * 3600


def notify(cmd: str, state_file: str, problems: list[str], log) -> None:
    try:
        prev = json.load(open(state_file))
    except (OSError, ValueError):
        prev = {}
    digest = hashlib.sha1("\n".join(sorted(problems)).encode()).hexdigest() if problems else ""
    now = time.time()
    if problems:
        if prev.get("hash") == digest and now - prev.get("ts", 0) < REPEAT:
            return
        text = "bgp-routes: проблемы\n" + "\n".join(f"- {p}" for p in problems)
    elif prev.get("hash"):
        text = "bgp-routes: проблемы устранены, листы обновлены"
    else:
        return
    try:
        subprocess.run([cmd, text], check=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        log(f"уведомление не отправлено: {e}")
        return  # состояние не обновляем - попробуем в следующий раз
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    json.dump({"hash": digest, "ts": now}, open(state_file, "w"))
