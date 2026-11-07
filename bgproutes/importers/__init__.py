"""Импортёры. Каждый модуль: NAME = "..." и класс Importer(name, params, ctx) с методом run() -> {IPv4Network: note}.
Новый источник = новый файл здесь (автообнаружение), строка в config.toml."""
import importlib
import pkgutil


class ImporterError(Exception):
    pass


def registry():
    reg = {}
    for m in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{m.name}")
        if hasattr(mod, "NAME"):
            mod.Importer.PARAMS, mod.Importer.REQUIRED = mod.PARAMS, mod.REQUIRED
            reg[mod.NAME] = mod.Importer
    return reg
