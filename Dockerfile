FROM python:3.11.6-alpine3.18 as builder

WORKDIR /build
RUN apk add gcc alpine-sdk
COPY requirements.txt ./
RUN pip install --no-cache-dir --target . -r requirements.txt

FROM python:3.11.6-alpine3.18
WORKDIR /opt/modbus2mqtt
ADD ./*.py ./
COPY --from=builder /build/ ./

ENTRYPOINT [ "python", "./modbus2mqtt.py", "-c", "/config.yaml" ]
