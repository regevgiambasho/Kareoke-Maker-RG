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

# whisperx 3.8.6 pins torch~=2.8.0. Install the matching torch stack first from
# TORCH_INDEX so pip does not pull the multi-GB CUDA build on CPU-only hosts.
# For GPU hardware build with: --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --index-url "$TORCH_INDEX" \
        torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 && \
    python -m pip install -r /tmp/requirements.txt

# Hugging Face Spaces run the container as uid 1000. Give that user a real
# home so ~/.karaoke_maker_v2 (projects, uploads, cache) and model caches are writable.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user . /home/user/app

EXPOSE 7860

# launcher.py mounts the chunked-upload / download routes on the same port
# as the Streamlit UI. Running `streamlit run app.py` directly would skip them.
CMD ["python", "launcher.py"]
