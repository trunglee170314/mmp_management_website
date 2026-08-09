#!/bin/sh
# Disposable remote only: simulate GitLab rejecting writes/branch permissions.
test ! -e /remote.git/reject-push
