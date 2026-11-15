#!/usr/bin/env bash
# The one place a variant name is resolved to the three files that define it.
#
# delta-verify.sh and channel-image.sh both need this mapping, and a second copy of a table is a
# second thing that can disagree with the first. The names are content, not positions: descriptor,
# dispatch and smc say which operation the manifest carries.
#
# Sourced, not run. It sets MANIFEST, EXPECT and BASE_BINARY in the caller.
#
# Usage: . ./delta-paths.sh; delta_paths descriptor
DELTA_ROOT=${DELTA_ROOT:-$RIG}

delta_paths() {
  case "${1:-}" in
    descriptor)
      MANIFEST=$DELTA_ROOT/fixtures/graft/manifest-descriptor-redirect-deploy.json
      EXPECT=$DELTA_ROOT/fixtures/graft/descriptor-redirect-deploy.expect.json
      ;;
    dispatch)
      MANIFEST=$DELTA_ROOT/fixtures/graft/manifest-dispatch-routing-deploy.json
      EXPECT=$DELTA_ROOT/fixtures/graft/dispatch-routing-deploy.expect.json
      ;;
    smc)
      MANIFEST=$DELTA_ROOT/fixtures/graft/manifest-smc-fill-deploy.json
      EXPECT=$DELTA_ROOT/fixtures/graft/smc-fill-deploy.expect.json
      ;;
    *) return 1;;
  esac
  BASE_BINARY=${BASE_BINARY:-$DELTA_ROOT/work/stageE/ati2mtag-s5_accessor_blob.sys}
  return 0
}

delta_names() { printf '%s\n' descriptor dispatch smc; }
