FROM python:3.11-slim

WORKDIR /srv

COPY worker.py ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "worker.py"]
