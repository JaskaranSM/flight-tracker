#!/bin/sh
rm -f /tmp/.X99-lock /tmp/.X0-lock
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 2
export DISPLAY=:99
exec python3 main.py
