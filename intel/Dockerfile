FROM python:3.12-slim

WORKDIR /app

RUN groupadd --system --gid 10001 intel \
    && useradd --system --uid 10001 --gid intel --home /app --shell /usr/sbin/nologin intel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN chown -R intel:intel /app
USER intel

EXPOSE 8090

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
