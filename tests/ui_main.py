from __future__ import annotations

import importlib

from app.core.migrations import upgrade_metastore

upgrade_metastore()

import app  # noqa: E402,F401

main_module = importlib.import_module("app.main")
importlib.reload(main_module)
