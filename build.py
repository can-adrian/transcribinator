"""Rez build script for transcribinator.

Invoked by rez via `build_command` in package.py:

    rez-build --install

Python dependencies are expected to come from the environment (rez packages
or site-packages); this script only copies the payload.

Outside rez it can also be used directly:

    python build.py install --to /some/where
"""
import os
import re
import shutil
import sys
from pathlib import Path

# index.html may live in static/ or beside server.py; both layouts work
PAYLOAD = ["server.py", "static", "index.html", "README.md",
           "requirements.txt", "transcribinator.sh"]

SOURCE = Path(os.environ.get("REZ_BUILD_SOURCE_PATH", Path(__file__).parent))
BUILD = Path(os.environ.get("REZ_BUILD_PATH", SOURCE / "build"))


def checkVersions():
    """package.py, server.py and index.html must all agree on the version."""
    pkg = (SOURCE / "package.py").read_text(encoding="utf-8")
    srv = (SOURCE / "server.py").read_text(encoding="utf-8")
    pagePath = next((p for p in (SOURCE / "static" / "index.html",
                                 SOURCE / "index.html") if p.is_file()), None)
    if pagePath is None:
        sys.exit("build: index.html not found in static/ or alongside server.py")
    page = pagePath.read_text(encoding="utf-8")
    found = {
        "package.py": re.search(r'^version\s*=\s*"([^"]+)"', pkg, re.M),
        "server.py": re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', srv, re.M),
        "index.html": re.search(r"EXPECTED_SERVER\s*=\s*'([^']+)'", page),
    }
    missing = [k for k, v in found.items() if not v]
    if missing:
        sys.exit(f"build: could not read version from {', '.join(missing)}")
    versions = {k: v.group(1) for k, v in found.items()}
    if len(set(versions.values())) != 1:
        detail = ", ".join(f"{k}={v}" for k, v in versions.items())
        sys.exit(f"build: version mismatch — {detail}. Bump them together.")
    return versions["server.py"]


def copyPayload(dest):
    dest.mkdir(parents=True, exist_ok=True)
    for item in PAYLOAD:
        src = SOURCE / item
        if not src.exists():
            print(f"build: skipping missing {item}")
            continue
        target = dest / item
        if src.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    print(f"build: payload -> {dest}")



def main():
    args = sys.argv[1:]
    doInstall = "install" in args

    version = checkVersions()
    print(f"build: transcribinator {version}")

    copyPayload(BUILD)

    if not doInstall:
        return

    if "--to" in args:
        install = Path(args[args.index("--to") + 1])
    else:
        install = Path(os.environ["REZ_BUILD_INSTALL_PATH"])
    copyPayload(install)
    print(f"build: installed to {install}")


if __name__ == "__main__":
    main()
