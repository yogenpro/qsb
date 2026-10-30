FROM python:3.12-slim@sha256:1148dbb014615300fa7e713b2f94f550c0d1c69780c0805f84f093ceee34dd0b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.source="https://github.com/yogenpro/qsb"
WORKDIR /app
COPY qsb_capture/ /app/qsb_capture/
COPY config/example.json /app/config/collector.json
RUN mkdir -p /data && chown 65532:65532 /data
USER 65532:65532
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["python", "-m", "qsb_capture.runtime", "check-health"]
ENTRYPOINT ["python", "-m", "qsb_capture.runtime"]
CMD ["run"]
