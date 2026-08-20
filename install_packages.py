#!/usr/bin/env python
"""Copy the locally-built rez packages Transcribinator needs to another user.

Most of the dependency packages currently live in one person's local package
path. To let someone else test the tool, those packages have to be copied into
their local path too.

Rather than hardcoding a list (which drifts), this resolves the context and
copies exactly the packages that resolved out of the source user's area.

    python install_packages.py --user jsmith --dry-run
    python install_packages.py --user jsmith

Nothing is deleted at the destination: rsync only adds and updates.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_REQUEST = ("python-3.11 fastapi uvicorn faster_whisper "
                   "imageio_ffmpeg argostranslate")
DEFAULT_SOURCE = "/ice/rez/packages/local/adts"
DEST_TEMPLATE = "/ice/rez/packages/local/{user}"

# printed inside the resolved context: every REZ_*_ROOT the resolve produced
DUMP = (
    "import os, json;"
    "print(json.dumps({k: v for k, v in os.environ.items()"
    " if k.startswith('REZ_') and k.endswith('_ROOT')}))"
)


def resolvedRoots(request):
    """Resolve the context and return the install root of each package."""
    cmd = ["rez-env"] + request.split() + ["--", "python", "-c", DUMP]
    print("resolving: rez-env " + request, flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit("resolve failed:\n" + (proc.stderr or proc.stdout))
    line = next((l for l in reversed(proc.stdout.strip().splitlines())
                 if l.startswith("{")), None)
    if not line:
        sys.exit("could not read package roots from the resolved context")
    return json.loads(line)


def packageNames(roots, sourceBase):
    """Package names whose install root lives under the source user's area."""
    base = os.path.normpath(sourceBase) + os.sep
    names = set()
    for root in roots.values():
        path = os.path.normpath(root)
        if path.startswith(base):
            names.add(path[len(base):].split(os.sep)[0])
    return sorted(names)


def dirSize(path):
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True,
                    help="username to copy the packages to")
    ap.add_argument("--request", default=DEFAULT_REQUEST,
                    help="rez request to resolve (default: %(default)s)")
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="source package path (default: %(default)s)")
    ap.add_argument("--dest", default=None,
                    help="destination package path (default: "
                         + DEST_TEMPLATE.format(user="<user>") + ")")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="show what would be copied, copy nothing")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    if not shutil.which("rsync"):
        sys.exit("rsync not found on PATH — it is needed to copy the packages")

    dest = args.dest or DEST_TEMPLATE.format(user=args.user)
    if os.path.normpath(dest) == os.path.normpath(args.source):
        sys.exit("source and destination are the same path")
    if not os.path.isdir(args.source):
        sys.exit(f"source path does not exist: {args.source}")

    roots = resolvedRoots(args.request)
    names = packageNames(roots, args.source)
    if not names:
        sys.exit(f"nothing resolved out of {args.source} — either the packages "
                 f"are already shared, or --source is wrong")

    print(f"\n{len(names)} package(s) to copy from {args.source}")
    total = 0
    for name in names:
        size = dirSize(os.path.join(args.source, name))
        total += size
        print(f"  {name:<28} {human(size):>8}")
    print(f"  {'total':<28} {human(total):>8}")
    print(f"\ndestination: {dest}")

    if args.dry_run:
        print("\n-- dry run, nothing copied --")
    elif not args.yes:
        if input("\nproceed? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("cancelled")

    failed = []
    for i, name in enumerate(names, 1):
        src = os.path.join(args.source, name) + os.sep
        dst = os.path.join(dest, name) + os.sep
        cmd = ["rsync", "-a", "--info=stats1"]
        if args.dry_run:
            cmd.append("--dry-run")
        cmd += [src, dst]
        print(f"\n[{i}/{len(names)}] {name}", flush=True)
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            failed.append(name)

    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    if args.dry_run:
        print("dry run complete — rerun without --dry-run to copy")
        return 0
    print(f"copied {len(names)} package(s) to {dest}")
    print("\nHave the user verify with:")
    print(f"    rez-env {args.request} -- python -c \"import fastapi, uvicorn, "
          f"faster_whisper, imageio_ffmpeg; print('resolve OK')\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
