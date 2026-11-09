FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PORT=7860

# fonts-dejavu-core + fontconfig: the subtitle renderer uses DejaVu Sans and
# fc-match to measure Hebrew/RTL word widths (same packages as the devcontainer).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libgomp1 \
    fonts-dejavu-core \
    fontconfig \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r /app/requirements.txt

COPY . /app

EXPOSE 7860

# launcher.py mounts the chunked-upload / download routes on the same port
# as the Streamlit UI. Running `streamlit run app.py` directly would skip them.
CMD ["python", "launcher.py"]
