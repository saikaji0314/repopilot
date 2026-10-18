# Trusted preparation only; candidate runs cannot build images or download dependencies.
# Linux amd64 example. Both stages use the same Debian bookworm userspace.
FROM node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS node
FROM python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /lib/x86_64-linux-gnu/libstdc++.so.6 /usr/local/lib/libstdc++.so.6
COPY --from=node /lib/x86_64-linux-gnu/libgcc_s.so.1 /usr/local/lib/libgcc_s.so.1
ENV LD_LIBRARY_PATH=/usr/local/lib
# npm is deliberately absent: prepare project dependencies in a trusted image separately.
RUN python3 --version && node --version
