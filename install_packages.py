#!/usr/bin/env python
"""Set a user up to run Transcribinator: rez packages and a shell alias.

Most of the dependency packages currently live in one person's local package
path. To let someone else test the tool, those packages have to be copied into
their local path too. Rather than hardcoding a list (which drifts), this
resolves the context and copies exactly the packages that resolved out of the
source user's area.

    python install_packages.py --user jsmith --dry-run   # show what would copy
    python install_packages.py --user jsmith             # copy the packages
    python install_packages.py --alias                   # add my own alias
    python install_packages.py --user jsmith --alias     # both

Nothing is deleted at the destination: rsync only adds and updates. The alias
is added to the *current* user's shell rc file, since another user's home
directory is normally not writable — for them, the printed line is what they
add themselves.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ALIAS_NAME = "transcribinator"
ALIAS_MARKER = "# added by transcribinator install_packages.py"
# A rez alias only exists inside a resolved context, so the shell alias wraps
# the whole resolve. --alias-target script points at the dev launcher instead.
ALIAS_REZ = f"rez-env {ALIAS_NAME} -- {ALIAS_NAME}"

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


def launcherPath():
    """The dev launcher next to this script, as an absolute path."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "transcribinator.sh")


def aliasCommand(target):
    """What the alias should run: the released rez package, or the launcher."""
    return ALIAS_REZ if target == "rez" else launcherPath()


def ensureAlias(rcPath, command, apply=True):
    """Add or update the alias line in a shell rc file. Returns a status word."""
    line = f"alias {ALIAS_NAME}='{command}'  {ALIAS_MARKER}"
    existing = []
    if os.path.isfile(rcPath):
        with open(rcPath, encoding="utf-8", errors="replace") as fh:
            existing = fh.read().splitlines()

    hits = [i for i, l in enumerate(existing) if ALIAS_MARKER in l
            or l.strip().startswith(f"alias {ALIAS_NAME}=")]
    if hits and existing[hits[0]].strip() == line:
        return "already set"
    if not apply:
        return "would update" if hits else "would add"

    if existing:                                  # keep a backup before editing
        try:
            with open(rcPath + ".bak", "w", encoding="utf-8") as fh:
                fh.write("\n".join(existing) + "\n")
        except OSError:
            pass

    if hits:
        for i in reversed(hits[1:]):              # drop any duplicates
            del existing[i]
        existing[hits[0]] = line
        status = "updated"
    else:
        if existing and existing[-1].strip():
            existing.append("")
        existing.append(line)
        status = "added"
    with open(rcPath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(existing) + "\n")
    return status


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=None,
                    help="username to copy the rez packages to")
    ap.add_argument("--alias", action="store_true",
                    help=f"add a '{ALIAS_NAME}' alias to your own shell rc file")
    ap.add_argument("--alias-target", choices=("rez", "script"), default="rez",
                    help="what the alias runs: the released rez package "
                         "(default) or the local transcribinator.sh")
    ap.add_argument("--rc", default=None,
                    help="shell rc file to edit (default: ~/.bashrc)")
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
    if not args.user and not args.alias:
        ap.error("give --user (to copy packages), --alias, or both")

    script = launcherPath()
    if args.alias:
        rc = os.path.expanduser(args.rc or "~/.bashrc")
        command = aliasCommand(args.alias_target)
        if args.alias_target == "script" and not os.path.isfile(script):
            print(f"WARNING: launcher not found at {script}", flush=True)
        status = ensureAlias(rc, command, apply=not args.dry_run)
        print(f"alias '{ALIAS_NAME}' -> {command}\n  {rc}: {status}")
        if status in ("added", "updated"):
            print(f"  run 'source {rc}' or open a new shell to pick it up")
        if not args.user:
            return 0
        print()

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
    print(f"\n{args.user} still needs the app itself. Have them:")
    print(f"  1. check the resolve:")
    print(f"       rez-env {args.request} -- python -c \"import fastapi, "
          f"uvicorn, faster_whisper, imageio_ffmpeg; print('resolve OK')\"")
    print(f"  2. make sure they can read the launcher:")
    print(f"       {script}")
    print(f"  3. add the alias in their own shell:")
    print(f"       python {os.path.abspath(__file__)} --alias")
    print(f"     (which sets: alias {ALIAS_NAME}='{ALIAS_REZ}')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
