#!/bin/bash
export WINEPREFIX=/home/dconnolly/xp64-rig/wineprefix
cd /home/dconnolly/xp64-rig/dtw
export WINEDEBUG=-all
export LD_PRELOAD=/home/dconnolly/xp64-rig/ttyshim.so
wine kd.exe -k 'com:port=\\.\COM1,baud=115200' < /tmp/opencode/kdin > /tmp/opencode/kdout.log 2>&1
