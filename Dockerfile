FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -m -u 10001 invoiceflow && mkdir -p /app/data && chown invoiceflow:invoiceflow /app/data
USER invoiceflow
EXPOSE 8000
CMD ["uvicorn", "invoiceflow_ai.api:app", "--host", "0.0.0.0", "--port", "8000"]
