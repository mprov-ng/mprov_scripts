# The image the script tests run in: Rocky 9 with what the scripts expect to
# find in an mProv image.  dnf and systemctl are replaced by stubs that log
# their arguments to /tmp/<name>.log, so the tests don't install packages or
# need a running systemd.
FROM rockylinux/rockylinux:9
RUN dnf -y install jq openssh python3-pip && dnf clean all
RUN for cmd in dnf systemctl; do \
      printf '#!/bin/bash\necho "$@" >> /tmp/%s.log\n' "$cmd" > /usr/local/bin/$cmd && \
      chmod 755 /usr/local/bin/$cmd; \
    done
