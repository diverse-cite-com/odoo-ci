#!/bin/bash
# Set up SSH for GitHub/GitLab access
# Requires SSH_KEY_GITHUB environment variable pointing to key file

set -e

mkdir -p ~/.ssh
chmod 700 ~/.ssh

cat > ~/.ssh/config << EOF
Host github.com
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null

Host git.bemade.org
  StrictHostKeyChecking no
  UserKnownHostsFile=/dev/null
EOF
chmod 600 ~/.ssh/config

if [ -n "$SSH_KEY_GITHUB" ]; then
  cp "$SSH_KEY_GITHUB" ~/.ssh/github_key
  chmod 600 ~/.ssh/github_key
  eval $(ssh-agent -s)
  ssh-add ~/.ssh/github_key
fi
