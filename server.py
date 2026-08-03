"""Transcribinator — offline call transcription, search and subtitling server.

Usage:
    pip install -r requirements.txt
    python server.py            # http://127.0.0.1:8765

Point the app at any media root folder (UI sidebar, or TRANSCRIBINATOR_MEDIA
env); it is scanned for supported media. Transcripts are saved next to each
movie as <name>_transcript.json.

Environment:
    WHISPER_MODEL              small|medium|large-v3   (default medium)
    TRANSCRIBINATOR_MEDIA      initial media root folder
    TRANSCRIBINATOR_CONFIG     user config dir  (default per-user config dir)
    TRANSCRIBINATOR_PORT       listen port      (default 8765)
    TRANSCRIBINATOR_NO_BROWSER set to skip auto-opening the browser

The pre-1.11 CALL_MEDIA / CALL_SEARCH_* names are still honoured as fallbacks.

Nothing is written inside the install directory, so this can be deployed as a
read-only package (e.g. a rez release).
"""
import getpass
import json
import os
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

import imageio_ffmpeg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

APP_NAME = "Transcribinator"
APP_SLUG = "transcribinator"
ROOT = Path(__file__).parent


def _env(name, legacy=(), default=None):
    """TRANSCRIBINATOR_<name>, falling back to the pre-1.11 variable names."""
    for key in (f"TRANSCRIBINATOR_{name}",) + tuple(legacy):
        val = os.environ.get(key)
        if val:
            return val
    return default


# Mutable state must never live inside the install root: a rez-released package
# is read-only. Everything the app writes goes to a per-user config dir,
# overridable with TRANSCRIBINATOR_CONFIG.
def _configBase():
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _configDir():
    override = _env("CONFIG", ("CALL_SEARCH_CONFIG",))
    if override:
        return Path(override).expanduser()
    return _configBase() / APP_SLUG


CONFIG_DIR = _configDir()
try:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    CONFIG_DIR = Path(tempfile.gettempdir()) / APP_SLUG
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = CONFIG_DIR / "config.json"        # media root, recursion, syn path
DEFAULT_SYN = CONFIG_DIR / "synonyms.json"

# one-time migrations: pre-1.8 in-folder config, and the pre-1.11 "call-search"
# config dir from before the rename
_legacyDir = _configBase() / "call-search"
for _old, _new in ((ROOT / "syn_config.json", CONFIG_PATH),
                   (ROOT / "synonyms.json", DEFAULT_SYN),
                   (_legacyDir / "config.json", CONFIG_PATH),
                   (_legacyDir / "synonyms.json", DEFAULT_SYN)):
    if _old.exists() and not _new.exists():
        try:
            _new.write_text(_old.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[migrate] {_old} -> {_new}", flush=True)
        except OSError:
            pass


def _defaultMedia():
    env = _env("MEDIA", ("CALL_MEDIA",))
    if env:
        return Path(env).expanduser()
    local = ROOT / "media"                       # dev checkout convenience
    if local.is_dir():
        return local
    return CONFIG_DIR / "media"


DEFAULT_MEDIA = _defaultMedia()
PORT = int(_env("PORT", ("CALL_SEARCH_PORT",), "8765"))
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".mp3", ".wav", ".m4a", ".flac"}
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
APP_VERSION = "1.11.1"

app = FastAPI(title=APP_NAME)

# ------------------------------------------------------------- native dialogs
_dialogLock = threading.Lock()
_PICK_CODE = {
    "folder": "print(fd.askdirectory() or '')",
    "open": ("print(fd.askopenfilename(filetypes="
             "[('JSON','*.json'),('All files','*.*')]) or '')"),
    "save": ("print(fd.asksaveasfilename(defaultextension='.json',"
             "filetypes=[('JSON','*.json')]) or '')"),
}


@app.get("/api/pick/{kind}")
def pickDialog(kind: str):
    if kind not in _PICK_CODE:
        raise HTTPException(400, "kind must be folder|open|save")
    if not _dialogLock.acquire(blocking=False):
        raise HTTPException(409, "a dialog is already open — check your taskbar")
    try:
        import sys
        code = ("import tkinter as tk, tkinter.filedialog as fd\n"
                "r=tk.Tk()\nr.withdraw()\nr.attributes('-topmost',True)\n"
                + _PICK_CODE[kind])
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise HTTPException(
                500, f"no native dialog available: {out.stderr.strip()[:200]}")
        return {"path": out.stdout.strip()}   # empty = user cancelled
    finally:
        _dialogLock.release()


# ---------------------------------------------------------------------- config
def _readCfg():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _writeCfg(**kw):
    cfg = _readCfg()
    cfg.update(kw)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _mediaRoot():
    root = Path(_readCfg().get("mediaRoot", DEFAULT_MEDIA))
    return root


def _recursive():
    return bool(_readCfg().get("recursive", True))


def _mediaFiles(root, pattern="*"):
    it = root.rglob(pattern) if _recursive() else root.glob(pattern)
    return it


def _activeSynPath():
    return Path(_readCfg().get("active", DEFAULT_SYN))


try:
    DEFAULT_MEDIA.mkdir(parents=True, exist_ok=True)
except OSError:
    pass                    # read-only install root: user picks a root in the UI


# stem = media file's path relative to the root, forward slashes, no extension
def _transcriptPath(stem):
    return _mediaRoot() / f"{stem}_transcript.json"


# human-readable sidecar format: header fields, then one line per segment /
# annotation; translations one line per language — still plain valid JSON
def _dumpTranscript(data):
    lines = ["{"]
    special = {"segments", "annotations", "translations"}
    for k, v in data.items():
        if k in special:
            continue
        lines.append(f'  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},')
    blocks = [("segments", data.get("segments", []), "rows")]
    if "translations" in data:
        blocks.append(("translations", data["translations"], "dict"))
    if "annotations" in data:
        blocks.append(("annotations", data["annotations"], "rows"))
    for bi, (name, content, kind) in enumerate(blocks):
        tail = "," if bi < len(blocks) - 1 else ""
        if kind == "rows":
            lines.append(f'  "{name}": [')
            for i, row in enumerate(content):
                t = "," if i < len(content) - 1 else ""
                lines.append("    " + json.dumps(
                    row, ensure_ascii=False, separators=(",", ": ")) + t)
            lines.append("  ]" + tail)
        else:
            lines.append(f'  "{name}": {{')
            keys = list(content)
            for i, kk in enumerate(keys):
                t = "," if i < len(keys) - 1 else ""
                lines.append(f'    {json.dumps(kk)}: ' + json.dumps(
                    content[kk], ensure_ascii=False,
                    separators=(",", ": ")) + t)
            lines.append("  }" + tail)
    lines.append("}")
    return "\n".join(lines)


def _writeTranscript(path, data):
    path.write_text(_dumpTranscript(data), encoding="utf-8")


# whose transcript is this sidecar? -> source file's basename, or None if unknown
def _sidecarOwner(path):
    try:
        head = path.open("r", encoding="utf-8").read(400)
    except OSError:
        return None
    m = re.search(r'"file"\s*:\s*"([^"]*)"', head)
    return PurePosixPath(m.group(1)).name if m else None


def _safeUnder(root, path):
    root, path = root.resolve(), path.resolve()
    return path == root or root in path.parents


# ---------------------------------------------------------------- transcription
_model = None
_modelLock = threading.Lock()
_status = {}          # stem -> queued | running:<pct> | error:<msg>
_jobQueue = queue.Queue()


def _getModel():
    global _model
    with _modelLock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_SIZE, device="auto", compute_type="auto")
        return _model


def _transcribe(relPath):
    rel = Path(relPath)
    stem = rel.with_suffix("").as_posix()
    _status[stem] = "running"
    tmpWav = Path(tempfile.gettempdir()) / f"{stem.replace('/', '_')}_extract.wav"
    try:
        # pre-extract clean mono 16k wav — sidesteps container/codec issues
        # that make whisper's internal decoder stop after a few seconds
        subprocess.run(
            [FFMPEG, "-y", "-i", str(_mediaRoot() / rel), "-vn",
             "-ac", "1", "-ar", "16000", "-map", "0:a:0", str(tmpWav)],
            check=True, capture_output=True)

        model = _getModel()
        print(f"[transcribe] {relPath}: starting", flush=True)
        t0 = time.time()
        segments, info = model.transcribe(
            str(tmpWav),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 1000},
            condition_on_previous_text=False,
        )
        outSegments = []
        lastPrinted = -10
        for seg in segments:
            outSegments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
                "words": [
                    {"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)}
                    for w in (seg.words or [])
                ],
            })
            pct = int(seg.end / info.duration * 100) if info.duration else 0
            _status[stem] = f"running:{min(pct, 99)}"
            if pct >= lastPrinted + 10:
                print(f"[transcribe] {relPath}: {pct}%  "
                      f"({seg.end:.0f}s / {info.duration:.0f}s)", flush=True)
                lastPrinted = pct
        data = {
            "file": rel.as_posix(),
            "language": info.language,
            "duration": round(info.duration, 3),
            "model": MODEL_SIZE,
            "created": time.time(),
            "segments": outSegments,
        }
        _writeTranscript(_transcriptPath(stem), data)
        _status.pop(stem, None)
        print(f"[transcribe] {relPath}: done — {info.duration:.0f}s of audio "
              f"in {time.time() - t0:.0f}s, {len(outSegments)} segments", flush=True)
    except Exception as exc:  # noqa: BLE001
        _status[stem] = f"error:{exc}"
    finally:
        tmpWav.unlink(missing_ok=True)


def _worker():
    while True:
        relPath = _jobQueue.get()
        _transcribe(relPath)
        _jobQueue.task_done()


threading.Thread(target=_worker, daemon=True).start()


# ------------------------------------------------------------------ translation
SUB_LANGS = {"sv": "Svenska", "es": "Español", "fr": "Français", "de": "Deutsch"}
_transStatus = {}          # "stem:lang" -> running:<pct> | error:<msg>
_transQueue = queue.Queue()


def _argosTranslator(lang):
    from argostranslate import translate
    installed = translate.get_installed_languages()
    src = next((l for l in installed if l.code == "en"), None)
    dst = next((l for l in installed if l.code == lang), None)
    return src.get_translation(dst) if src and dst else None


_INSTALL_HINT = (
    "language pack en->{lang} missing — while online run:  "
    "python -c \"import argostranslate.package as p; p.update_package_index(); "
    "[p.install_from_path(x.download()) for x in p.get_available_packages() "
    "if x.from_code=='en' and x.to_code=='{lang}']\"")


def _translateJob(stem, lang):
    key = f"{stem}:{lang}"
    _transStatus[key] = "running"
    try:
        path = _transcriptPath(stem)
        data = json.loads(path.read_text(encoding="utf-8"))
        tr = _argosTranslator(lang)
        if tr is None:
            _transStatus[key] = "error:" + _INSTALL_HINT.format(lang=lang)
            return
        segs = data.get("segments", [])
        print(f"[translate] {stem} -> {lang}: {len(segs)} segments", flush=True)
        t0 = time.time()
        out = []
        for i, s in enumerate(segs):
            out.append(tr.translate(s["text"]) if s["text"] else "")
            if i % 10 == 0:
                _transStatus[key] = f"running:{int(i / max(len(segs), 1) * 100)}"
        data.setdefault("translations", {})[lang] = out
        _writeTranscript(path, data)
        _transStatus.pop(key, None)
        print(f"[translate] {stem} -> {lang}: done in {time.time()-t0:.0f}s",
              flush=True)
    except ModuleNotFoundError:
        _transStatus[key] = ("error:argostranslate not installed — "
                             "pip install argostranslate")
    except Exception as exc:  # noqa: BLE001
        _transStatus[key] = f"error:{exc}"


def _transWorker():
    while True:
        stem, lang = _transQueue.get()
        _translateJob(stem, lang)
        _transQueue.task_done()


threading.Thread(target=_transWorker, daemon=True).start()


@app.get("/api/subLangs")
def subLangs():
    installed = []
    try:
        for code in SUB_LANGS:
            if _argosTranslator(code) is not None:
                installed.append(code)
    except ModuleNotFoundError:
        pass
    return {"langs": SUB_LANGS, "installed": installed}


@app.post("/api/translate/{stem:path}")
def startTranslate(stem: str, lang: str):
    if lang not in SUB_LANGS:
        raise HTTPException(400, f"lang must be one of {list(SUB_LANGS)}")
    path = _transcriptPath(stem)
    if not _safeUnder(_mediaRoot(), path) or not path.exists():
        raise HTTPException(404, "no transcript for this recording")
    data = json.loads(path.read_text(encoding="utf-8"))
    if lang in data.get("translations", {}):
        return {"status": "done"}
    key = f"{stem}:{lang}"
    if str(_transStatus.get(key, "")).startswith("running"):
        return {"status": _transStatus[key]}
    _transStatus[key] = "running"
    _transQueue.put((stem, lang))
    return {"status": "queued"}


@app.get("/api/translateStatus/{stem:path}")
def translateStatus(stem: str, lang: str):
    key = f"{stem}:{lang}"
    if key in _transStatus:
        return {"status": _transStatus[key]}
    path = _transcriptPath(stem)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if lang in data.get("translations", {}):
            return {"status": "done"}
    return {"status": "none"}


# ------------------------------------------------------------------ conversion
# Browsers can't decode ProRes/DNxHD/etc. For those we make an h264 sidecar
# named <stem>_converted.mp4 next to the source, and play that instead.
CONVERTED_SUFFIX = "_converted.mp4"
_convStatus = {}           # stem -> running:<pct> | error:<msg>
_convQueue = queue.Queue()


def _convertedPath(stem):
    return _mediaRoot() / f"{stem}{CONVERTED_SUFFIX}"


def _mediaDuration(path):
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True,
                         text=True, errors="replace")
    m = re.search(r"Duration: (\d+):(\d\d):(\d\d\.\d+)", out.stderr or "")
    if not m:
        return 0.0
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def _convertJob(relPath):
    rel = Path(relPath)
    stem = rel.with_suffix("").as_posix()
    _convStatus[stem] = "running"
    src = _mediaRoot() / rel
    dst = _convertedPath(stem)
    tmp = dst.with_suffix(".part.mp4")
    try:
        dur = _mediaDuration(src)
        print(f"[convert] {relPath}: starting ({dur:.0f}s)", flush=True)
        t0 = time.time()
        proc = subprocess.Popen(
            [FFMPEG, "-y", "-i", str(src),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-vf", r"scale=-2:min(1080\,ih)",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
             "-progress", "pipe:1", "-nostats", str(tmp)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace")
        lastPrinted = -10
        for line in proc.stdout:
            if line.startswith("out_time_us=") and dur:
                us = line.strip().split("=")[1]
                if us.isdigit():
                    pct = min(int(int(us) / 1e6 / dur * 100), 99)
                    _convStatus[stem] = f"running:{pct}"
                    if pct >= lastPrinted + 20:
                        print(f"[convert] {relPath}: {pct}%", flush=True)
                        lastPrinted = pct
        proc.wait()
        if proc.returncode != 0:
            tail = (proc.stderr.read() or "").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"ffmpeg exit {proc.returncode}")
        tmp.replace(dst)
        _convStatus.pop(stem, None)
        print(f"[convert] {relPath}: done in {time.time() - t0:.0f}s -> "
              f"{dst.name}", flush=True)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        _convStatus[stem] = f"error:{exc}"


def _convWorker():
    while True:
        relPath = _convQueue.get()
        _convertJob(relPath)
        _convQueue.task_done()


threading.Thread(target=_convWorker, daemon=True).start()


@app.post("/api/convert/{relPath:path}")
def startConvert(relPath: str):
    root = _mediaRoot()
    path = root / relPath
    if (not _safeUnder(root, path) or not path.is_file()
            or path.suffix.lower() not in MEDIA_EXTS):
        raise HTTPException(404, "unknown media file")
    stem = Path(relPath).with_suffix("").as_posix()
    if _convertedPath(stem).exists():
        return {"status": "done"}
    if str(_convStatus.get(stem, "")).startswith("running"):
        return {"status": _convStatus[stem]}
    _convStatus[stem] = "running"
    _convQueue.put(relPath)
    return {"status": "queued"}


@app.get("/api/convertStatus/{stem:path}")
def convertStatus(stem: str):
    if _convertedPath(stem).exists():
        return {"status": "done"}
    return {"status": _convStatus.get(stem, "none")}


# ------------------------------------------------------------------------- api
@app.get("/api/mediaRoot")
def getMediaRoot():
    root = _mediaRoot()
    return {"path": str(root.resolve()), "exists": root.is_dir(),
            "recursive": _recursive()}


@app.post("/api/mediaRoot")
async def setMediaRoot(request: Request):
    body = await request.json()
    updates = {}
    raw = (body.get("path") or "").strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_dir():
            raise HTTPException(404, f"not a folder: {root}")
        updates["mediaRoot"] = str(root)
    if "recursive" in body:
        updates["recursive"] = bool(body["recursive"])
    if not updates:
        raise HTTPException(400, "nothing to update")
    _writeCfg(**updates)
    _status.clear()
    return getMediaRoot()


@app.get("/api/videos")
def listVideos():
    root = _mediaRoot()
    items = []
    if not root.is_dir():
        return items
    files = [f for f in _mediaFiles(root)
             if f.is_file() and f.suffix.lower() in MEDIA_EXTS
             and not f.name.endswith(CONVERTED_SUFFIX)]
    for f in sorted(files, key=lambda p: p.relative_to(root).as_posix().lower()):
        rel = f.relative_to(root)
        stem = rel.with_suffix("").as_posix()
        tPath = _transcriptPath(stem)
        if tPath.exists():
            owner = _sidecarOwner(tPath)
            state = "done" if owner in (None, rel.name) else f"conflict:{owner}"
        else:
            state = _status.get(stem, "none")
        items.append({"file": rel.as_posix(), "stem": stem, "status": state,
                      "size": f.stat().st_size,
                      "converted": _convertedPath(stem).exists()})
    return items


@app.post("/api/transcribe/{relPath:path}")
def startTranscribe(relPath: str):
    root = _mediaRoot()
    path = root / relPath
    if (not _safeUnder(root, path) or not path.is_file()
            or path.suffix.lower() not in MEDIA_EXTS):
        raise HTTPException(404, "unknown media file")
    stem = Path(relPath).with_suffix("").as_posix()
    tPath = _transcriptPath(stem)
    if tPath.exists():
        owner = _sidecarOwner(tPath)
        if owner not in (None, path.name):
            raise HTTPException(
                409, f"{tPath.name} belongs to '{owner}' — rename one of the "
                     f"files to resolve the collision")
    if str(_status.get(stem, "")).startswith(("queued", "running")):
        return {"status": _status[stem]}
    tPath.unlink(missing_ok=True)
    _status[stem] = "queued"
    _jobQueue.put(relPath)
    return {"status": "queued"}


@app.get("/api/transcript/{stem:path}")
def getTranscript(stem: str):
    path = _transcriptPath(stem)
    if not _safeUnder(_mediaRoot(), path) or not path.exists():
        raise HTTPException(404, "no transcript")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/search")
def searchAll(q: str):
    q = q.strip().lower()
    if len(q) < 2:
        return []
    root = _mediaRoot()
    results = []
    if not root.is_dir():
        return results
    for path in sorted(_mediaFiles(root, "*_transcript.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        stem = path.relative_to(root).as_posix()[:-len("_transcript.json")]
        for seg in data["segments"]:
            if q in seg["text"].lower():
                results.append({
                    "file": data["file"],
                    "stem": stem,
                    "start": seg["start"],
                    "text": seg["text"],
                })
    return results[:200]


@app.get("/media/{relPath:path}")
def serveMedia(relPath: str):
    root = _mediaRoot()
    path = root / relPath
    if not _safeUnder(root, path) or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path)


# -------------------------------------------------------------------- synonyms
def _validSynDict(data):
    return isinstance(data, dict) and all(
        isinstance(k, str) and isinstance(v, list)
        and all(isinstance(a, str) for a in v)
        for k, v in data.items()
    )


@app.get("/api/synonyms")
def getSynonyms():
    path = _activeSynPath()
    groups = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if _validSynDict(data):
                groups = data
        except Exception:  # noqa: BLE001
            pass
    return {"path": str(path), "exists": path.exists(), "groups": groups}


@app.post("/api/synonyms")
async def setSynonyms(request: Request):
    data = await request.json()
    if not _validSynDict(data):
        raise HTTPException(400, "expected {key: [aliases, ...]}")
    path = _activeSynPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path)}


@app.post("/api/synonyms/file")
async def switchSynFile(request: Request):
    body = await request.json()
    rawPath, mode = body.get("path", "").strip(), body.get("mode")
    if not rawPath:
        raise HTTPException(400, "no path given")
    path = Path(rawPath).expanduser()
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    if mode == "create":
        if path.exists():
            raise HTTPException(409, f"already exists: {path}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(400, f"cannot create: {exc}") from exc
    elif mode == "load":
        if not path.exists():
            raise HTTPException(404, f"not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"not valid JSON: {exc}") from exc
        if not _validSynDict(data):
            raise HTTPException(400, "file is not {key: [aliases, ...]} shaped")
    else:
        raise HTTPException(400, "mode must be 'load' or 'create'")
    _writeCfg(active=str(path))
    return getSynonyms()


# ----------------------------------------------------------------- annotations
@app.get("/api/whoami")
def whoami():
    return {"user": getpass.getuser(), "version": APP_VERSION}


@app.post("/api/annotations/{stem:path}")
async def setAnnotations(stem: str, request: Request):
    path = _transcriptPath(stem)
    if not _safeUnder(_mediaRoot(), path) or not path.exists():
        raise HTTPException(404, f"no transcript file found: {path.name}")
    annos = await request.json()
    if not isinstance(annos, list) or not all(
        isinstance(a, dict)
        and isinstance(a.get("time"), (int, float))
        and isinstance(a.get("text"), str)
        for a in annos
    ):
        raise HTTPException(400, "expected [{time: seconds, text: str}, ...]")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["annotations"] = sorted(
        ({"time": round(float(a["time"]), 3), "text": a["text"].strip(),
          **({"user": a["user"].strip()} if isinstance(a.get("user"), str)
             and a["user"].strip() else {})}
         for a in annos if a["text"].strip()),
        key=lambda a: a["time"],
    )
    _writeTranscript(path, data)
    return {"ok": True, "annotations": data["annotations"]}


@app.post("/api/quit")
def quitServer():
    def _stop():
        time.sleep(0.4)          # let the response flush first
        os._exit(0)
    threading.Thread(target=_stop, daemon=True).start()
    return {"ok": True}


def _indexPath():
    """index.html may live in static/ or right next to server.py."""
    for candidate in (ROOT / "static" / "index.html", ROOT / "index.html"):
        if candidate.is_file():
            return candidate
    return None


@app.get("/")
def index():
    path = _indexPath()
    if path is None:
        raise HTTPException(
            500, f"index.html not found — expected it in {ROOT / 'static'} "
                 f"or {ROOT}. Make sure it sits beside server.py or in a "
                 f"static subfolder.")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


def _isOurServer(port):
    """True if something on this port is a Transcribinator instance."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/whoami", timeout=1.5) as r:
            return "version" in _json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def _portFree(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _openBrowser(url):
    if _env("NO_BROWSER", ("CALL_SEARCH_NO_BROWSER",)):
        return
    import webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    import sys

    port = PORT
    if not _portFree(port):
        if _isOurServer(port):
            # already running — just bring it up in the browser and exit
            url = f"http://127.0.0.1:{port}"
            print(f"{APP_NAME} is already running at {url} — opening it.",
                  flush=True)
            _openBrowser(url)
            sys.exit(0)
        # something else owns the port: move to the next free one
        port = next((p for p in range(PORT + 1, PORT + 21) if _portFree(p)), None)
        if port is None:
            sys.exit(f"ERROR: ports {PORT}-{PORT + 20} are all in use. "
                     "Set TRANSCRIBINATOR_PORT to choose another.")
        print(f"NOTE: port {PORT} is taken by another program — using {port}.",
              flush=True)

    url = f"http://127.0.0.1:{port}"
    if _indexPath() is None:
        print(f"WARNING: index.html not found in {ROOT / 'static'} or {ROOT} — "
              "the page will not load until it is in one of those.", flush=True)
    print(f"{APP_NAME} v{APP_VERSION} — model={MODEL_SIZE}  "
          f"media={_mediaRoot()}\n  config={CONFIG_DIR}  {url}\n"
          "  Close this window, or use Quit in the app, to stop the server.",
          flush=True)
    _openBrowser(url)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)
