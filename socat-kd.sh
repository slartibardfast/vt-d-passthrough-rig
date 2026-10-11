#!/bin/bash
while true; do
  socat PTY,link=/tmp/com1,raw,echo=0 UNIX-CONNECT:/tmp/opencode/kd.sock
  sleep 1
done
