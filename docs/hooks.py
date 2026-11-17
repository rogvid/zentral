"""MkDocs build hook: self-host Mermaid.

The theme would otherwise load Mermaid from a public CDN, which makes every
reader's browser contact a third party. The pinned copy from web/'s
devDependencies is published with the site instead. Missing it is an error,
never a silent fallback to the CDN.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).parents[1] / "web/node_modules/mermaid/dist/mermaid.min.js"
TARGET = "assets/javascripts/mermaid.min.js"


def on_post_build(config: Any, **_: Any) -> None:
    if not SOURCE.is_file():
        raise SystemExit("Mermaid not found: run `npm ci` in web/ before building the docs")
    destination = Path(config["site_dir"]) / TARGET
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, destination)
