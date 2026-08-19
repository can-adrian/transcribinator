name = "transcribinator"

version = "1.15.1"       # keep in sync with APP_VERSION in server.py
                        # (build.py fails the build if these drift)

description = "Transcribinator — offline transcription, search, annotation " \
              "and subtitling for recorded client calls. Runs a local web " \
              "app; no data leaves the machine."

authors = ["adrian"]

# --- dependencies ------------------------------------------------------------
# The app's python imports, to be resolved as packages in your repo.
# Uncomment / rename to match the package names your studio uses.
requires = [
    "python-3.9+<3.13",
    # "fastapi",
    # "uvicorn",
    # "faster_whisper",
    # "imageio_ffmpeg",
    # "argostranslate",     # optional: subtitle translation
]

# optional at runtime — the app degrades gracefully without it
# weak_requires = ["argostranslate"]

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

    # Studio-wide defaults can be set here, e.g.
    #   env.TRANSCRIBINATOR_MEDIA = "/shows/{env.SHOW}/client_calls"
    #   env.WHISPER_MODEL = "/tools/models/faster-whisper-medium"
    #   env.TRANSCRIBINATOR_ARGOS_DIR = "/tools/models/argos"
    # Users can still change the media root in the UI; it is stored per-user.
    pass
