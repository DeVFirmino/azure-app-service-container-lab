FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --create-home --uid 10001 appuser

COPY main.py ./

# The process always listens on 8080, so the App Service WEBSITES_PORT
# setting must say 8080 as well. EXPOSE documents the port, it does not
# publish it.
USER appuser
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
