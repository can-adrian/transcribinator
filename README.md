# Transcribinator — offline transcription, search and subtitles for client calls

Everything runs on localhost. Network access is needed only for one-time model
downloads (Whisper, and optionally the translation packs); after that the
machine can be fully air-gapped.

    python server.py          # -> http://127.0.0.1:8765   (dev / standalone)

The browser opens automatically. Launching a second time just opens the
existing instance rather than failing; if the port is taken by something else,
the next free one is used. Stop it with the ⏻ button in the app, or by closing
the console.

The startup line reports the version and active media root, e.g.
`Transcribinator v1.13.0 — model=medium  media=D:\shows\...`.

## Setup

Python dependencies (see `requirements.txt`) are expected to be provided by
the environment — a rez package, or however your studio provisions
site-packages. Nothing here installs them.

Two model sets must also be staged locally, since there is no internet access
at runtime:

**Speech model** — `WHISPER_MODEL` takes either a size name (`small`,
`medium`, `large-v3`) or a path to a converted faster-whisper model folder on
disk:

    set WHISPER_MODEL=\\tools\models\faster-whisper-medium

A size name only works if that model is already in the local HuggingFace
cache — per user, `%USERPROFILE%\.cache\huggingface\hub` (or
`~/.cache/huggingface/hub`), overridable with `HF_HOME`. A path avoids the
cache question entirely.

With no internet, also set `HF_HUB_OFFLINE=1` so model loading reads the cache
directly instead of waiting on a network timeout first. `package.py` sets this
in `post_commands()`.

**Translation packs** (optional, for subtitles) — put the `.argosmodel` files
in a folder and point at it:

    set TRANSCRIBINATOR_ARGOS_DIR=\\tools\models\argos

They are installed on first use, and logged as `[argos]` lines. Without them
everything except subtitle translation works normally.

Both can be set studio-wide in `post_commands()` in `package.py`.

## Command line

With no arguments the app starts the web UI. It can also run headless, which
is handy for batch or overnight work:

    transcribinator transcribe CALL.mov
    transcribinator transcribe /shows/hnd/calls -r
    transcribinator transcribe /shows/hnd/calls -r -c --translate sv,fr

    -r, --recursive   recurse into subfolders
    -f, --force       redo work even if the output exists
    -c, --convert     also write a browser-playable _converted.mp4
    --translate       comma separated: sv, es, fr, de

Sidecars are written next to each source file exactly as the UI writes them,
so anything processed this way is immediately searchable in the app. Existing
outputs are skipped unless `--force` is given, which makes re-running over a
folder cheap. Each step (transcribe / convert / translate) reports separately
and one failing does not stop the others; the exit code is non-zero if
anything failed.

Without rez, the same commands are `python server.py transcribe ...`.

## Using it

**Root folder** (sidebar): point the app at any folder; it is scanned for
media (`.mp4 .mov .mkv .webm .m4v .mp3 .wav .m4a .flac`). "Include subfolders"
toggles recursion. Browse via the `…` button (native OS dialog) or type a path
and press Enter. The choice persists across restarts.

**Transcribe**: click the badge next to a recording. Progress shows as a
percentage and a bar; the console logs `[transcribe]` lines. Transcripts are
saved as `<moviename>_transcript.json` **next to the movie**, so a movie and
its transcript (with annotations and translations) travel together.

**Search** (bottom frame): type a term, Enter makes it a colored chip, up to
10. Matches highlight in the transcript, appear as colored ticks on the marker
strip, and as mini-bars under every recording in the sidebar. Enter on an empty
input cycles matches; `/` focuses the input.

- Matching is **whole-word** by default (`ilm` does not match `film`).
- `*` = any run of characters within a word (`fire*` -> fireball, fired).
- `?` = exactly one character (`s??` -> sun, set, six).
- Digit terms auto-expand to spoken forms: `1140` also matches "eleven forty",
  "11 40", "one one four zero".
- ALL-CAPS terms expand to spelled forms: `TTB` also matches "t t b", "t.t.b.",
  "tee tee bee". Composites work too: `TTB1140`.
- Hover a chip to see exactly what it is matching.

**Tabs**: TRANSCRIPT · WORD CLOUD · SYNONYMS · ANNOTATIONS.

- *Word cloud*: most frequent content words in the call; click one to search it.
- *Synonyms*: group misspellings/nicknames under a key — searching the key or
  any alias matches the whole group. Stored in a JSON file you choose
  (Load / Create new, or leave the path empty to browse).
- *Annotations*: right-click the marker strip to add a note at that timecode,
  or "+ Add at current time". Notes are stamped with your OS username, show as
  flags on the strip, and are saved **inside the transcript JSON**.

**Subtitles**: the "Subs:" dropdown on the player. *Original* works instantly
on any transcribed call. Picking a language translates on demand (offline,
Argos), caches the result in the transcript JSON, and shows it as a subtitle
track. Nothing is burned into the video.

**Unplayable codecs**: browsers decode h264/vp9/av1 only. If a file's codec
can't play (ProRes, DNxHD, ...), a bar appears offering **Convert for
playback** — ffmpeg writes an h264 copy named `<name>_converted.mp4` next to
the source and plays that from then on. Transcription, search and annotations
work on the original regardless, so converting is only needed to *watch* it.

**Refresh (⟳)** next to the title: re-reads everything from disk — useful after
hand-editing any JSON.

## Deploying as a rez package

    rez-build --install
    rez-release

Then, for users:

    rez-env transcribinator -- transcribinator

`package.py` lists the python library dependencies commented out — enable and
rename them to match the packages in your repo.

Nothing is written inside the install root, so the released package can be
read-only. Per-user state lives in a config dir:

- Windows: `%APPDATA%\transcribinator\`
- Linux/macOS: `~/.config/transcribinator/`

containing `config.json` (media root, recursion, active synonyms file) and the
default `synonyms.json`. Override the location with `TRANSCRIBINATOR_CONFIG`.
Transcripts always live next to their movies, never in the install.

A studio-wide default media root can be set in `post_commands()` in
`package.py` (e.g. from a `$SHOW` variable); users can still change it in the
UI and their choice persists per-user.

To run two instances side by side, set `TRANSCRIBINATOR_PORT`.

The app was previously called Call Search; the old `CALL_MEDIA` /
`CALL_SEARCH_*` environment variables still work, and settings from the old
config folder are migrated automatically on first run.

`build.py` fails the build if `package.py`'s version and `server.py`'s
`APP_VERSION` disagree — the page and server check each other at runtime, and
this extends the same guarantee to releases.

## Notes

- Model size: `WHISPER_MODEL=large-v3 python server.py` for accuracy, `small`
  for speed. GPU used automatically if CUDA is present.
- Audio is pre-extracted with a bundled ffmpeg before transcription; this
  avoids container quirks that make decoders stop after a few seconds.
- Transcript JSON is written one line per segment for readability.
- Sidecars are matched by filename. If two media files share a name
  (`call.mp3` / `call.mp4`), the app flags a **conflict** instead of showing
  the wrong transcript — rename one of them.
- The page and server exchange a version handshake; a red banner means the two
  files are from different versions. Both should always be replaced together.
- Server binds to 127.0.0.1 only. Exposing it on a LAN would need auth, and
  the native file dialogs would open on the server machine.
