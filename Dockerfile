FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY . .

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8787
CMD ["python", "main.py"]
