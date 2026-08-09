FROM postgres:16-alpine
RUN apk add --no-cache git openssh-client age
COPY deploy/backup-git.sh deploy/backup-decrypt.sh /opt/
