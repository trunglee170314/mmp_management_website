FROM mmp-backup-tools:local
RUN apk add --no-cache openssh-server && adduser -D -s /bin/sh git \
    && echo 'git:disposable-fixture-only' | chpasswd
COPY deploy/tests/git-server.sh deploy/tests/reject-push.sh /opt/
ENTRYPOINT ["/bin/sh", "/opt/git-server.sh"]
