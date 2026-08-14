FROM python@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff

ENV PYTHONHASHSEED=0
ENV PYTHONPATH=/app/packages/data_contracts/src:/app/packages/routing/src

WORKDIR /app

RUN addgroup -S -g 65532 arrive90 \
    && adduser -S -D -H -u 65532 -G arrive90 arrive90

COPY --chown=65532:65532 packages/data_contracts/src ./packages/data_contracts/src
COPY --chown=65532:65532 packages/routing/src ./packages/routing/src
COPY --chown=65532:65532 benchmarks ./benchmarks

USER 65532:65532

ENTRYPOINT ["python", "benchmarks/run_milestone6.py"]
