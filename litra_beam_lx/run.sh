#!/usr/bin/with-contenv bashio
# Entry point for the Litra Beam LX add-on.
#
# `with-contenv` makes the Supervisor-provided environment (including
# SUPERVISOR_TOKEN, used to fetch the MQTT broker details) available. The bridge
# reads its options from /data/options.json and the MQTT service from the
# Supervisor automatically — see litra_ble/config.py.

bashio::log.info "Starting Litra Beam LX → Home Assistant bridge..."

if ! bashio::services.available "mqtt"; then
  bashio::log.warning "No MQTT broker found. Install & start the Mosquitto broker add-on."
fi

exec python -m litra_ble.bridge
