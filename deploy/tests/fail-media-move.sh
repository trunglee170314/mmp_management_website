#!/bin/sh
# Test-only fault injection. Never installed into any production service.
for argument in "$@"; do
    case "$argument" in /media/*) echo 'Injected media move failure' >&2; exit 1;; esac
done
exec /bin/mv "$@"
