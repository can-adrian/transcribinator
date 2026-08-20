name = "transcribinator"

version = "1.19.0"       # keep in sync with APP_VERSION in server.py
                        # (build.py fails the build if these drift)

description = "Transcribinator — offline transcription, search, annotation " \
              "and subtitling for recorded client calls. Runs a local web " \
              "app; no data leaves the machine."

authors = ["adrian"]

# --- dependencies ------------------------------------------------------------
# Pinned to python-3.11 because the converted packages carry hard
# python==3.11.7 requirements.
requires = [
    "python-3.11",
    "fastapi",
    "uvicorn",
    "faster_whisper",
    "imageio_ffmpeg",

    # Subtitle translation only. The app runs fine without it (the Subs
    # dropdown falls back to the original language), so drop this line if the
    # packages are troublesome.
    "argostranslate",

    # argostranslate imports stanza, which imports torch — even though nothing
    # here uses a GPU. The CUDA torch builds fail to import unless every
    # nvidia_* lib dir is on LD_LIBRARY_PATH, so pin the CPU build explicitly.
    # Note "torch-2.7.1" alone resolves to the cu128 variant; the -cpu suffix
    # is required.
    "torch-2.7.1-cpu",
]

tools = ["transcribinator"]

uuid = "transcribinator-8f2c1d7e-4a90-4c1b-9c3e-6b5d0a2f7e11"

build_command = "python {root}/build.py {install}"


def commands():
    env.TRANSCRIBINATOR_ROOT = "{root}"

    alias("transcribinator", "python {root}/server.py")


def post_commands():
    # No internet at runtime: skip the HuggingFace reachability check so model
    # loading uses the local cache immediately instead of timing out first.
    env.HF_HUB_OFFLINE = "1"

    # Staged model data, shared read-only.
    env.WHISPER_MODEL = "/ice/shared/adts/neural_networks"
    env.TRANSCRIBINATOR_ARGOS_DIR = "/ice/shared/adts/language_packs"

    # A default media root can be set here too, e.g.
    #   env.TRANSCRIBINATOR_MEDIA = "/shows/{env.SHOW}/client_calls"
    # Users can change it in the UI; their choice is stored per-user.
