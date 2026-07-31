# PRC-17 — the container image gate.yml's own header comment calls "option 2": a hosted
# runner carrying Godot_v4.7.1-stable_linux.x86_64 + xvfb-run + a software-GL Mesa stack
# baked in, so tier 2/3 (sprite atlas, sfx determinism, godot boots, terrain parsers agree,
# game renders, menu renders, accessibility) run on every PR without a self-hosted runner.
#
# Base is ubuntu:24.04 — the same distro family as `ubuntu-latest` (gate.yml's tier-1 job)
# and the dev machine this repo's coverage/contrast thresholds were tuned on (see
# CLAUDE.md: "game renders asserts coverage ~0.95 / 99 tones... tuned on this box's Mesa
# llvmpipe"), so this image's Mesa build is close kin rather than an unrelated stack.
#
# Godot is downloaded once at image-build time and baked in — not re-downloaded per job,
# unlike gate.yml's tier-1 "Install Godot" step, because this image exists specifically to
# amortise that cost across every run that pulls it.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV GODOT_VERSION=4.7.1-stable

# python3.12 + venv matches gate.yml's `actions/setup-python@v5 python-version: "3.12"`.
# libsndfile1 backs the `soundfile` wheel tier 2's `sfx determinism` imports at runtime —
# it is a C library, not a pure-python dependency, and pip alone cannot supply it.
# xvfb + the Mesa GL libraries are decision 052's mechanism: a native Linux Godot binary
# under `xvfb-run` against Mesa llvmpipe is what makes `game renders`/`menu renders`/
# `accessibility` possible at all without a real desktop. git-lfs is required because this
# job's checkout uses `lfs: true` (tier 2/3 read real sprite/audio bytes — see gate.yml's
# header comment on why tier 1 alone gets `lfs: false`).
#
# The Mesa/GL packages alone are not enough to launch Godot's GUI binary at all, even under
# Xvfb: measured directly, a bare `libgl1-mesa-dri libglx-mesa0 libegl-mesa0` image fails
# before it ever reaches a display connection, with `libXcursor.so.1: cannot open shared
# object file` — Godot's Linux build links these X11/Wayland client libraries unconditionally
# for cursor/input handling, `--headless` or not, so they are load-bearing even though this
# job never shows a cursor. `libgl1`/`libglu1-mesa` provide `libGL.so.1` itself, which the
# `libgl1-mesa-dri`/`libglx-mesa0` *driver* packages assume is already present rather than
# provide themselves.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3.12 python3.12-venv python3-pip \
        curl unzip ca-certificates git git-lfs \
        xvfb libsndfile1 \
        libgl1-mesa-dri libglx-mesa0 libegl-mesa0 mesa-vulkan-drivers libgl1 libglu1-mesa \
        libxcursor1 libxinerama1 libxi6 libxrandr2 libxrender1 libxext6 libx11-6 \
        libxkbcommon0 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
        libasound2t64 libpulse0 libudev1 libdbus-1-3 \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# `actions/checkout` in a *container* job does not guarantee the checked-out tree's UID
# matches the container user the way a host-level job does — measured here as `git`
# refusing every checkout-reading check (`game data`'s `git ls-files -z data`, and every
# other check that goes through `discover_documents()`) with "detected dubious ownership in
# repository". A wildcard is safe here specifically because this image never runs anything
# other than this repo's own checkout — it is not a general-purpose git image shared across
# untrusted repos.
RUN git config --system --add safe.directory '*'

# Bake in the exact Godot build CLAUDE.md names and gate.yml's tier-1 job downloads fresh
# every run — this image gets it once, at build time.
RUN curl -fsSL -o /tmp/godot.zip \
        "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/Godot_v${GODOT_VERSION}_linux.x86_64.zip" \
    && unzip -q /tmp/godot.zip -d /opt/godot \
    && rm /tmp/godot.zip \
    && chmod +x "/opt/godot/Godot_v${GODOT_VERSION}_linux.x86_64" \
    && ln -s "/opt/godot/Godot_v${GODOT_VERSION}_linux.x86_64" /usr/local/bin/godot

# tools/toolpaths.py's `godot()` checks `$LF_GODOT` first, ahead of every glob/PATH
# fallback (see its own docstring) — set explicitly rather than relying on the `godot`
# on PATH, because the sharded parity harness and a couple of checks resolve the binary
# by that exact env var.
ENV LF_GODOT=/opt/godot/Godot_v4.7.1-stable_linux.x86_64

# tools/toolpaths.py's `needs_virtual_display()` only wraps a native-Linux Godot launch in
# `xvfb-run` when BOTH the binary is native Linux AND `$DISPLAY` is already set — a
# genuinely headless box with no `$DISPLAY` at all is read as "nothing to hide a window
# from" and `want_window=False` callers (tools/shot.py, check_game_renders,
# check_menu_renders, check_accessibility) then try to open a real, unwrapped window,
# which fails outright in a container with no X server present at all. A bare
# `ubuntu:24.04` container has no `$DISPLAY` by default, so without this line
# `needs_virtual_display()` is silently False and every rendered check fails to launch
# Godot at all — not a slow/degraded run, a hard failure with a confusing GL error. The
# value itself is never dereferenced against a real server: `xvfb_prefix()` always invokes
# `xvfb-run -a`, which ignores any inherited `$DISPLAY` and picks its own free display
# number, so this only needs to be *set*, not valid. Confirmed by running `godot boots`
# inside this image with and without the line — see tools/ci/README-verification notes in
# the PR description this Dockerfile shipped with.
ENV DISPLAY=:99

WORKDIR /workspace

# Dependencies pinned to match this repo's own `.venv` (see `.venv/bin/pip freeze` on the
# dev machine) rather than "whatever pip resolves today" — a container that silently drifts
# from the interactively-verified environment defeats the point of baking one at all.
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir \
        jsonschema==4.26.0 \
        numpy==2.5.1 \
        pillow==12.3.0 \
        soundfile==0.14.0 \
        PyYAML==6.0.3

# No COPY of the repo here — the workflow mounts/checks out the real tree at job time via
# `actions/checkout`, so this image only ever carries the toolchain, never a stale copy of
# game data.
