# Shiftly — all-in-one image: FastAPI + SQLite + static frontend.
# Works on any container host (Back4App, Railway, Fly, VPS, docker run ...).
# The app reads PORT from the environment (hosts inject it) and defaults to 8081.
FROM python:3.11-slim

WORKDIR /app

# requirements first so a code-only change doesn't redo the dependency layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "main.py"]
