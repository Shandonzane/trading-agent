# Always-on live paper-trading monitor. Paper only; keys come from the host's env vars:
#   ALPACA_API_KEY, ALPACA_SECRET_KEY, FEED_TOKEN (any long random string)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "cli.py", "serve"]
