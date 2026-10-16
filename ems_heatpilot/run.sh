#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# with-contenv reicht die Umgebung des Containers durch – darunter SUPERVISOR_TOKEN,
# den Schlüssel, mit dem das Add-on Home Assistant lesen darf.
cd /app || exit 1
exec python3 -m heatpilot.main
