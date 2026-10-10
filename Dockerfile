FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# run as an unprivileged user
RUN useradd --system --uid 10001 app && chown -R app /srv
USER app
EXPOSE 8080
CMD ["python", "-m", "app.bot.main"]