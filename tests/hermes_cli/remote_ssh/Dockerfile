FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server tmux git python3 procps \
    && rm -rf /var/lib/apt/lists/* && rm -f /etc/ssh/ssh_host_* \
    && useradd -m -s /bin/sh dev && passwd -d dev \
    && mkdir -p /run/sshd /home/dev/.ssh /home/dev/projects \
    && chmod 700 /home/dev/.ssh && chown -R dev:dev /home/dev
COPY stub.py /opt/remote-test-stub.py
RUN printf '#!/bin/sh\nexec python3 /opt/remote-test-stub.py hermes "$@"\n' > /usr/local/bin/hermes \
    && printf '#!/bin/sh\nexec python3 /opt/remote-test-stub.py claude "$@"\n' > /usr/local/bin/claude \
    && chmod +x /usr/local/bin/hermes /usr/local/bin/claude \
    && printf 'PasswordAuthentication no\nPermitRootLogin no\nAllowUsers dev\n' > /etc/ssh/sshd_config.d/remote-test.conf
CMD ["sh", "-c", "ssh-keygen -A && exec /usr/sbin/sshd -D -e"]
