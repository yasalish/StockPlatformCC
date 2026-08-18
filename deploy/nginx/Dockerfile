# syntax=docker/dockerfile:1
#
# nginx for بورس‌نگار — TLS termination, gzip + brotli, static files, proxy.
#
# ---------------------------------------------------------------------------
# WHY DEBIAN AND NOT THE OFFICIAL `nginx` IMAGE
#
# The order requires brotli, and the official nginx image has no brotli module.
# The usual answer — compiling ngx_brotli from GitHub against nginx's source —
# is a poor fit here: it needs a full build toolchain, and it depends on GitHub
# and on nginx.org being reachable from an Iranian network at build time.
#
# Debian ships BOTH pieces as packages built from the same nginx source:
#     nginx                              1.22.1
#     libnginx-mod-http-brotli-filter    1.0.0~rc-3   (dynamic compression)
#     libnginx-mod-http-brotli-static    1.0.0~rc-3   (pre-compressed .br files)
# so the module ABI matches the server by construction — the failure mode that
# makes hand-compiled dynamic modules fragile cannot occur. Debian's packaging
# drops the load_module lines into /etc/nginx/modules-enabled/ automatically.
#
# It also means one apt mirror covers everything, and DEBIAN_MIRROR below can
# point at a domestic one.
# ---------------------------------------------------------------------------
ARG REGISTRY=

FROM ${REGISTRY}debian:bookworm-slim

# Swap in a domestic Debian mirror when the default is slow or blocked, e.g.
#   --build-arg DEBIAN_MIRROR=mirror.arvancloud.ir
# bookworm images use the deb822 sources file; older layouts use sources.list.
ARG DEBIAN_MIRROR=deb.debian.org

RUN set -eux; \
    if [ "${DEBIAN_MIRROR}" != "deb.debian.org" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
            [ -f "$f" ] && sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g; s|security.debian.org|${DEBIAN_MIRROR}|g" "$f" || true; \
        done; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        nginx \
        libnginx-mod-http-brotli-filter \
        libnginx-mod-http-brotli-static \
        curl \
        ca-certificates \
        openssl; \
    rm -rf /var/lib/apt/lists/*; \
    # Fail the build now, not at 3am, if the mirror served an nginx without the
    # brotli modules — the whole point of this base image. These two .so paths
    # are shipped BY the packages, so checking them is exact.
    test -e /usr/lib/nginx/modules/ngx_http_brotli_filter_module.so; \
    test -e /usr/lib/nginx/modules/ngx_http_brotli_static_module.so; \
    # The /etc/nginx/modules-enabled/*.conf symlinks that nginx.conf includes are
    # NOT shipped in either package — they are created by a postinst maintainer
    # script, whose numeric prefix and very execution are things a Docker build
    # should not have to bet on. Create them here if postinst did not, so the
    # load_module lines exist deterministically either way. `ln -sf` over an
    # existing symlink is a no-op, so this never double-loads a module.
    for m in filter static; do \
        src="/usr/share/nginx/modules-available/mod-http-brotli-$m.conf"; \
        test -e "$src"; \
        if ! grep -rqs "ngx_http_brotli_${m}_module" /etc/nginx/modules-enabled/; then \
            ln -sf "$src" "/etc/nginx/modules-enabled/50-mod-http-brotli-$m.conf"; \
        fi; \
    done; \
    grep -rq ngx_http_brotli_filter_module /etc/nginx/modules-enabled/; \
    # Logs to the container's stdout/stderr so `docker compose logs nginx` works.
    ln -sf /dev/stdout /var/log/nginx/access.log; \
    ln -sf /dev/stderr /var/log/nginx/error.log

# Debian ships a default site that would answer on :80 alongside ours.
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/*.conf

COPY nginx.conf            /etc/nginx/nginx.conf
COPY conf.d/               /etc/nginx/conf.d/
COPY snippets/             /etc/nginx/snippets/
COPY entrypoint.sh         /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /etc/nginx/tls /var/cache/nginx

# `nginx -t` at build time catches a syntax error in the committed config before
# it can take the site down. It needs a certificate to exist, so a throwaway one
# is generated, tested against, and deleted — the real certificate arrives on a
# volume at run time.
#
# Two things must exist for `nginx -t` to run at all, neither of which does at
# build time: a certificate file (ssl_certificate is opened, not just parsed)
# and a resolvable `web` host (nginx refuses to start with an upstream name it
# cannot resolve). Both are faked inside this single RUN — the /etc/hosts entry
# does not persist into the image, since Docker mounts a fresh one at run time,
# and the certificate is deleted immediately below.
RUN set -eux; \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -subj "/CN=build-test" \
        -keyout /etc/nginx/tls/privkey.pem \
        -out /etc/nginx/tls/fullchain.pem 2>/dev/null; \
    echo "127.0.0.1 web" >> /etc/hosts; \
    nginx -t; \
    nginx -V 2>&1 | tr ' ' '\n' | grep -i brotli || echo "(brotli loaded as a dynamic module)"; \
    rm -f /etc/nginx/tls/privkey.pem /etc/nginx/tls/fullchain.pem

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1/nginx-health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
