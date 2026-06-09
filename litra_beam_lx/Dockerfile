ARG BUILD_FROM
FROM ${BUILD_FROM}

# bluez provides `bluetoothctl`, which we use to pair/trust the lights against
# the host's BlueZ (reached over the host D-Bus socket). git lets us install the
# bridge package straight from the source repo.
RUN apk add --no-cache bluez git

# Install the bridge. Pinned to a ref so rebuilds are reproducible; bump
# LITRA_REF (or point it at a tag) when releasing a new version.
ARG LITRA_REF=main
RUN pip install --no-cache-dir \
        "git+https://github.com/alexvales/litra-ble-mqtt@${LITRA_REF}"

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
