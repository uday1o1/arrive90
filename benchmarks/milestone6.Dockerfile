FROM python@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e

ENV PYTHONHASHSEED=0
ENV PYTHONPATH=/app/packages/data_contracts/src:/app/packages/routing/src

WORKDIR /app

COPY packages/data_contracts/src ./packages/data_contracts/src
COPY packages/routing/src ./packages/routing/src
COPY benchmarks ./benchmarks

ENTRYPOINT ["python", "benchmarks/run_milestone6.py"]
