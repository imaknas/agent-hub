# bin/linux-test 用的 Linux 環境:平台需要的最少東西(Python 3.13 + PyYAML、git、ps、sqlite3),一般使用者(不是 root)。
FROM python:3.13-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git procps sqlite3 jq >/dev/null \
    && rm -rf /var/lib/apt/lists/* && pip install -q pyyaml==6.0.3
RUN useradd -m u
USER u
RUN git config --global user.name t && git config --global user.email t@t
