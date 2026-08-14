FROM python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e

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
    typing-inspection==0.4.4

COPY packages/data_contracts/src /opt/arrive90/packages/data_contracts/src
COPY packages/decision/src /opt/arrive90/packages/decision/src
COPY packages/service/src /opt/arrive90/packages/service/src
COPY benchmarks/run_milestone5.py /opt/arrive90/benchmarks/run_milestone5.py

ENV PYTHONPATH=/opt/arrive90/packages/data_contracts/src:/opt/arrive90/packages/decision/src:/opt/arrive90/packages/service/src

ENTRYPOINT ["python", "/opt/arrive90/benchmarks/run_milestone5.py"]
