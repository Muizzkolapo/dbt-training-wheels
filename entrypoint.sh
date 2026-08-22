#!/bin/bash

# Fix macOS-specific SSH config options for Linux compatibility
# See: https://developer.apple.com/library/archive/technotes/tn2449/_index.html

if [ -f ~/.ssh/config ]; then
    mkdir -p /tmp/.ssh
    echo "IgnoreUnknown UseKeychain,AddKeysToAgent" > /tmp/.ssh/config
    cat ~/.ssh/config >> /tmp/.ssh/config
    export GIT_SSH_COMMAND="ssh -F /tmp/.ssh/config"
fi

# gh-stack is baked into the image, so nothing to install here. The one thing that
# can't be: credentials. Say so at startup rather than leaving it to be discovered on
# the deploy screen.
if ! gh auth status > /dev/null 2>&1; then
    echo "[entrypoint] gh is not authenticated - pull requests won't be opened automatically."
    echo "[entrypoint] Start with: GH_TOKEN=\$(gh auth token) docker-compose up"
fi

exec "$@"
