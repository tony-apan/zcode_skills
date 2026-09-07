#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
git -C "$ROOT" config core.hooksPath .githooks
printf '%s\n' "Installed repository hooks: core.hooksPath=.githooks"
printf '%s\n' "Every push now requires a current github MODE=RELEASE_GATE PASS audit."
