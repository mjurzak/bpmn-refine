FROM python:3.12-slim

WORKDIR /app

# install uv
RUN pip install uv

# copy dependency spec first for layer caching
COPY pyproject.toml .
COPY .python-version .

# install deps into the system python (no venv needed inside Docker)
RUN uv pip install --system -e ".[dev]"

COPY backend/ ./backend/
COPY tests/ ./tests/

ENV PYTHONPATH=/app/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
