FROM python:3.12-slim
WORKDIR /app
COPY bot.py ideas.py .
CMD ["python", "-u", "bot.py"]
