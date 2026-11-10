FROM python:3.15-alpine

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt \
    && apk add --no-cache tzdata
COPY app /opt/app
COPY config.yaml /opt/config.yaml
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.type="addon" io.hass.version="0.3.5"
CMD ["/run.sh"]
