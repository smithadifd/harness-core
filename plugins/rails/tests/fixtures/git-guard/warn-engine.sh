#!/bin/bash
# A stand-in engine for the dependencies suite (D5). It writes one stderr
# line, sleeps STANDIN_SLEEP seconds when that is set, then prints the
# verdict STANDIN_DECISION names (no_opinion when unset). Test fixture only:
# the guard itself reads neither variable.
printf '%s\n' 'warn-engine: a warning on stderr' >&2
[ -z "${STANDIN_SLEEP:-}" ] || sleep "$STANDIN_SLEEP"
case "${STANDIN_DECISION:-no_opinion}" in
  ask) printf '%s\n' '{"decision":"ask","reason":"push_force"}' ;;
  deny) printf '%s\n' '{"decision":"deny","reason":"push_force"}' ;;
  *) printf '%s\n' '{"decision":"no_opinion","reason":"not_gated"}' ;;
esac
