FROM python:3.12-slim

WORKDIR /app

# Install system deps for git tools and clipboard (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install llm-code
COPY pyproject.toml .
COPY llm_code/ llm_code/
COPY README.md LICENSE ./
RUN pip install --no-cache-dir .

# Default config directory
RUN mkdir -p /root/.llm-code

ENTRYPOINT ["llm-code"]
