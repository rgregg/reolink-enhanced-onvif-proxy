FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY reolink_onvif_proxy/ reolink_onvif_proxy/

RUN pip install --no-cache-dir .

ENTRYPOINT ["reolink-onvif-proxy"]
CMD ["-c", "/config.yml"]
