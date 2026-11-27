#!/usr/bin/env bash
# The candidate INF must point the 290X at a Hawaii install section and carry the Win7 Hawaii settings.
#
# This is the precondition for the boot, not the proof of the fix. The boot's idle-word series is the
# receipt's evidence; this check makes the candidate itself auditable before a boot is spent.
set -euo pipefail
cd "$(dirname "$0")"

INF=work/candidates/CA178340-hawaii.inf
[ -s "$INF" ] || { printf 'no candidate at %s\n' "$INF" >&2; exit 1; }

grep -Eq '= *ati2mtag_Hawaii, *PCI\\VEN_1002&DEV_67B0' "$INF" ||
  { printf 'the candidate does not bind 67B0 to ati2mtag_Hawaii\n' >&2; exit 1; }
grep -qx '\[ati2mtag_Hawaii\]' "$INF" ||
  { printf 'the candidate has no ati2mtag_Hawaii section\n' >&2; exit 1; }
grep -qx '\[ati2mtag_Hawaii_SoftwareDeviceSettings\]' "$INF" ||
  { printf 'the candidate has no Hawaii device-settings section\n' >&2; exit 1; }
grep -q 'PP_EnableBAPM' "$INF" ||
  { printf 'the candidate does not carry the Hawaii power setting\n' >&2; exit 1; }
grep -q 'DalAllowDPrefSwitchingForGLSync' "$INF" ||
  { printf 'the candidate does not carry the Hawaii display setting\n' >&2; exit 1; }

printf 'candidate INF: 67B0 -> ati2mtag_Hawaii, with the Hawaii settings\n'
