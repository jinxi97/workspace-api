FROM python:3.13-slim

# Install system deps needed for git-based Python dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy the application into the container.
COPY . /app

# Install the application dependencies.
WORKDIR /app
RUN uv sync --frozen --no-cache

# Run the application.
CMD ["uv", "run", "fastapi", "run", "main.py", "--port", "8080", "--host", "0.0.0.0"]