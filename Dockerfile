FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# libopus is required for any voice connection (send or receive).
# git is required to pip-install py-cord from its GitHub PR (see requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
