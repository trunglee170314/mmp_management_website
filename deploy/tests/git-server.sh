#!/bin/sh
set -eu
umask 077
if [ ! -f /run/secrets/backup_ssh_key ]; then
    ssh-keygen -q -t ed25519 -N '' -f /run/secrets/backup_ssh_key
    age-keygen -o /run/secrets/backup_age_identity
    age-keygen -y /run/secrets/backup_age_identity > /run/secrets/backup_age_recipients
fi
mkdir -p /home/git/.ssh /run/sshd
cp /run/secrets/backup_ssh_key.pub /home/git/.ssh/authorized_keys
chown -R git:git /home/git/.ssh
chmod 700 /home/git/.ssh
chmod 600 /home/git/.ssh/authorized_keys
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key
fi
{ printf '[git-server]:2222 '; cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub; } > /run/secrets/backup_known_hosts
if [ ! -f /remote.git/HEAD ]; then
    git init --bare -b main /remote.git
fi
cp /opt/reject-push.sh /remote.git/hooks/pre-receive
chmod +x /remote.git/hooks/pre-receive
chown -R git:git /remote.git
exec /usr/sbin/sshd -D -e -p 2222 -h /etc/ssh/ssh_host_ed25519_key \
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o PermitRootLogin=no
