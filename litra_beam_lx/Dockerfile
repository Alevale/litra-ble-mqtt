ARG BUILD_FROM
FROM ${BUILD_FROM}

# bluez provides `bluetoothctl`, which we use to pair/trust the lights against
# the host's BlueZ (reached over the host D-Bus socket). git lets us install the
# bridge package straight from the source repo.
RUN apk add --no-cache bluez git

# Install the bridge from the repo. BUILD_VERSION (passed by the Supervisor from
# config.yaml's version) is referenced here so that bumping the add-on version
# busts this layer's cache and the update actually pulls the new code, instead
# of reusing a stale cached `pip install` layer.
ARG BUILD_VERSION
ARG LITRA_REF=main
RUN echo "building litra-ble-mqtt ${BUILD_VERSION}" && \
    pip install --no-cache-dir \
        "git+https://github.com/Alevale/litra-ble-mqtt@${LITRA_REF}"

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
