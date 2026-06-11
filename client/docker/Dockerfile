# Headless, scriptable OSRS (rev-180 RuneLite) client.
#
# Runs the shaded client under a virtual X display (Xvfb) so synthetic input
# (xdotool) and screenshots (ImageMagick `import`) work with no host GUI and no
# macOS Accessibility permission. The 3D scene renders in software (the GPU
# plugin is disabled by default), so no OpenGL/LWJGL natives are required.
#
# The client jar and cache are mounted at runtime (see run.sh), not baked in, so
# rebuilding the host jar does not require rebuilding this image.
FROM eclipse-temurin:11-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11-utils \
        xdotool \
        imagemagick \
        scrot \
        fonts-dejavu-core \
        libgl1-mesa-dri \
        libgl1-mesa-glx \
        libxrender1 \
        libxtst6 \
        libxi6 \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Software GL fallback in case any code path touches OpenGL.
ENV LIBGL_ALWAYS_SOFTWARE=1 \
    DISPLAY=:99

WORKDIR /client

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY drive_login.sh /usr/local/bin/drive_login.sh
COPY spectate_loop.sh /usr/local/bin/spectate_loop.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/drive_login.sh /usr/local/bin/spectate_loop.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
