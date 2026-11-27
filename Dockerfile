FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN mkdir -p /app/benchmarks /app/data/warehouse

CMD ["sh", "-c", "python scripts/contract_validator.py --raw-dir data/raw --contract config/data_contracts.json --emit-lineage benchmarks/lineage_manifest.json && python scripts/run_local_warehouse.py --mode full --benchmark && python scripts/analytics_mining.py && python -m unittest discover -s tests -v"]
