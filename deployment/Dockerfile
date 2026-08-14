FROM python@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/opt/arrive90/packages/data_contracts/src:/opt/arrive90/packages/decision/src:/opt/arrive90/packages/service/src

RUN python -m pip install --no-cache-dir --disable-pip-version-check --no-compile \
    annotated-doc==0.0.5 \
    annotated-types==0.8.0 \
    anyio==4.14.2 \
    click==8.4.2 \
    fastapi==0.141.1 \
    gtfs-realtime-bindings==2.1.0 \
    h11==0.16.0 \
    idna==3.18 \
    protobuf==7.35.1 \
    pydantic==2.13.4 \
    pydantic-core==2.46.4 \
    starlette==1.6.0 \
    typing-extensions==4.16.0 \
    typing-inspection==0.4.4 \
    uvicorn==0.51.0 \
    && addgroup -S -g 65532 arrive90 \
    && adduser -S -D -H -u 65532 -G arrive90 arrive90 \
    && mkdir -p /state \
    && chown 65532:65532 /state

WORKDIR /opt/arrive90

COPY --chown=65532:65532 packages/data_contracts/src ./packages/data_contracts/src
COPY --chown=65532:65532 packages/decision/src ./packages/decision/src
COPY --chown=65532:65532 packages/service/src ./packages/service/src

USER 65532:65532

ENTRYPOINT ["python", "-m", "arrive90_service.cli"]
CMD ["--host", "127.0.0.1", "--port", "8000", "--state", "/state/arrive90.sqlite3"]
