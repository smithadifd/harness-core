#!/bin/bash
# A stand-in engine for the never-allow suite (N3): it prints an allow
# verdict, which the adapter must refuse as invalid engine output.
printf '%s\n' '{"decision":"allow","reason":"accepted"}'
