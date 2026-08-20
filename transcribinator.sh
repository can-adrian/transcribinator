#!/usr/bin/env bash
#
# Launch Transcribinator inside a resolved rez context.
#
#   ./transcribinator.sh                          # web app, last used media root
#   ./transcribinator.sh /shows/hnd/calls         # web app, opening that folder
#   ./transcribinator.sh transcribe /shows/hnd/calls -r --translate sv
#   ./transcribinator.sh --version
#
# Everything after the script name is passed straight through to server.py.
#
# Each setting below can be overridden from the environment, e.g.
#   WHISPER_MODEL=/tmp/faster-whisper-small ./transcribinator.sh
#
set -euo pipefail

# --- site settings -----------------------------------------------------------
# Packages to resolve. Pinned to python-3.11 because the converted packages
# carry hard python==3.11.7 requirements.
REZ_REQUEST="${TRANSCRIBINATOR_REZ_REQUEST:-python-3.11 fastapi uvicorn faster_whisper imageio_ffmpeg argostranslate torch-2.7.1-cpu}"

# Speech model: a folder holding model.bin, config.json, tokenizer.json, vocabulary.txt
WHISPER_MODEL="${WHISPER_MODEL:-/ice/shared/adts/neural_networks}"

# Translation packs: a folder of .argosmodel files (optional)
TRANSCRIBINATOR_ARGOS_DIR="${TRANSCRIBINATOR_ARGOS_DIR:-/ice/shared/adts/language_packs}"

# No internet at runtime: don't let huggingface_hub wait on a network timeout
HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
# -----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$HERE/server.py"

if [[ ! -f "$SERVER" ]]; then
    echo "ERROR: server.py not found next to this script ($HERE)" >&2
    exit 1
fi
if ! command -v rez-env >/dev/null 2>&1; then
    echo "ERROR: rez-env not found on PATH" >&2
    exit 1
fi
if [[ ! -d "$WHISPER_MODEL" ]]; then
    echo "WARNING: WHISPER_MODEL is not a folder: $WHISPER_MODEL" >&2
    echo "         transcription will fail until this points at the model" >&2
fi
if [[ ! -d "$TRANSCRIBINATOR_ARGOS_DIR" ]]; then
    echo "NOTE: no translation packs at $TRANSCRIBINATOR_ARGOS_DIR" >&2
    echo "      subtitles will work in the original language only" >&2
fi

# REZ_REQUEST is deliberately unquoted: it is a list of package requests.
# The variables are passed via env so they survive whatever the site's rez
# config does with the parent environment.
exec rez-env $REZ_REQUEST -- env \
    WHISPER_MODEL="$WHISPER_MODEL" \
    TRANSCRIBINATOR_ARGOS_DIR="$TRANSCRIBINATOR_ARGOS_DIR" \
    HF_HUB_OFFLINE="$HF_HUB_OFFLINE" \
    python "$SERVER" "$@"
