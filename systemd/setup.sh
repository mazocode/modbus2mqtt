#!/bin/bash
DIR=$(dirname "$0")
ln -s ${DIR}/modbus2mqtt.service /etc/systemd/system/
systemctl daemon-reload
