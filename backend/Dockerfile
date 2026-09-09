FROM python:3.12-slim

WORKDIR /app

# install uv
RUN pip install uv

# copy dependency metadata first for layer caching
COPY pyproject.toml .
COPY uv.lock .
COPY .python-version .

# install locked deps into the system python (no venv needed inside Docker)
RUN uv export --frozen --extra dev --no-emit-project --format requirements.txt --output-file requirements.txt \
    && uv pip install --system -r requirements.txt \
    && rm requirements.txt

COPY backend/ ./backend/
# Read-only dataset comparison endpoints resolve this versioned snapshot at runtime.
# The source PMo corpus and experiment results are not part of the application image.
COPY data/eval/ ./data/eval/

ENV PYTHONPATH=/app/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
