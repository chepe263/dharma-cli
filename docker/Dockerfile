# Imagen de KiroCrew + stack de desarrollo horneado (Node, .NET, PHP, build).
# Extiende la imagen oficial (Debian 13 trixie) y añade las herramientas que los
# agentes necesitan para escribir y ejecutar código. Python/git/curl ya vienen.
#
# Construir:   docker compose build   (el compose apunta a este Dockerfile)
# Versiones fijadas por ARG para builds reproducibles; súbelas cuando quieras.
FROM ghcr.io/kirodotdev/kirocrew:stable

ARG NODE_MAJOR=22
ARG DOTNET_CHANNEL=8.0

# Todo el aprovisionamiento como root; al final se restaura el usuario kirocrew.
USER root

# ── Básicas de build + PHP + utilidades ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates gnupg \
        php-cli php-mysql php-mbstring php-xml php-curl php-zip \
        default-mysql-client unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Node LTS (repo oficial NodeSource) + Composer (PHP) ───────────────────────
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://getcomposer.org/installer | php -- \
        --install-dir=/usr/local/bin --filename=composer

# ── .NET SDK (script oficial de Microsoft, a /usr/local/dotnet) ───────────────
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel "${DOTNET_CHANNEL}" \
        --install-dir /usr/local/dotnet \
    && ln -s /usr/local/dotnet/dotnet /usr/local/bin/dotnet \
    && rm -f /tmp/dotnet-install.sh
ENV DOTNET_ROOT=/usr/local/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1

# Volver al usuario sin privilegios con el que corre KiroCrew.
USER kirocrew

# Verificación en build: si algo no quedó, el build falla aquí (no en runtime).
RUN node --version && npm --version && php --version \
    && composer --version && dotnet --version && python3 --version
