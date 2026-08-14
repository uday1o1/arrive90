FROM python@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff

RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    annotated-doc==0.0.5 \
    annotated-types==0.8.0 \
    anyio==4.14.2 \
    fastapi==0.141.1 \
    h11==0.16.0 \
    httpcore2==2.10.0 \
    httpx2==2.10.0 \
    idna==3.18 \
    pydantic==2.13.4 \
    pydantic-core==2.46.4 \
    starlette==1.6.0 \
    truststore==0.10.4 \
    typing-extensions==4.16.0 \
    typing-inspection==0.4.4 \
    && addgroup -S -g 65532 arrive90 \
    && adduser -S -D -H -u 65532 -G arrive90 arrive90

COPY --chown=65532:65532 packages/data_contracts/src /opt/arrive90/packages/data_contracts/src
COPY --chown=65532:65532 packages/decision/src /opt/arrive90/packages/decision/src
COPY --chown=65532:65532 packages/service/src /opt/arrive90/packages/service/src
COPY --chown=65532:65532 benchmarks/run_milestone5.py /opt/arrive90/benchmarks/run_milestone5.py

ENV PYTHONPATH=/opt/arrive90/packages/data_contracts/src:/opt/arrive90/packages/decision/src:/opt/arrive90/packages/service/src

USER 65532:65532

ENTRYPOINT ["python", "/opt/arrive90/benchmarks/run_milestone5.py"]
