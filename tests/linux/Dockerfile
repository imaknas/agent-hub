# The Linux environment for bin/linux-test: the least the platform needs (Python 3.13 + PyYAML, git, ps, sqlite3), an
# ordinary user (not root).
FROM python:3.13-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git procps sqlite3 jq >/dev/null \
    && rm -rf /var/lib/apt/lists/* && pip install -q pyyaml==6.0.3
RUN useradd -m u
USER u
RUN git config --global user.name t && git config --global user.email t@t
