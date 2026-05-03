FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir discord.py python-dotenv aiohttp pytz

CMD ["python", "bot.py"]