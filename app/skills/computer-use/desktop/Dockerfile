# Throwaway desktop for the computer-use skill: TigerVNC's Xvnc with a
# window manager, a terminal and a browser. The VNC server has no password,
# so only publish it on the host's loopback (desktop.sh does this).
FROM debian:bookworm-slim

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    tigervnc-standalone-server \
    fluxbox xterm \
    firefox-esr \
    fonts-dejavu-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run the desktop as an unprivileged user, so whatever the model types in the
# terminal can't touch root-owned files in the container.
RUN useradd --create-home --shell /bin/bash desktop
COPY entrypoint.sh /usr/local/bin/desktop-entrypoint
RUN chmod 755 /usr/local/bin/desktop-entrypoint

USER desktop
WORKDIR /home/desktop

# Screen size; the computer toolset driver refuses anything over 1920x1200.
ENV GEOMETRY=1280x800
EXPOSE 5900

ENTRYPOINT ["/usr/local/bin/desktop-entrypoint"]
