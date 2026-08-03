name = "transcribinator"

version = "1.11.1"       # keep in sync with APP_VERSION in server.py
                        # (build.py fails the build if these drift)

description = "Transcribinator — offline transcription, search, annotation " \
              "and subtitling for recorded client calls. Runs a local web " \
              "app; no data leaves the machine."

authors = ["adrian"]

# --- dependencies ------------------------------------------------------------
# Adjust to match the packages that exist in your repo. The python libraries
# below are the app's actual imports; if you don't have rez packages for them,
# build with --vendor to bake them into the package payload instead:
#
#     rez-build --install -- --vendor
#
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

    # vendored python deps, present only when built with --vendor
    import os.path
    vendor = os.path.join("{root}", "vendor")
    if os.path.isdir(vendor):
        env.PYTHONPATH.append(vendor)

    alias("transcribinator", "python {root}/server.py")


def post_commands():
    # A studio-wide default root can be set here, e.g.
    #   env.TRANSCRIBINATOR_MEDIA = "/shows/{env.SHOW}/client_calls"
    # Users can still change it in the UI; their choice is stored per-user.
    pass
