from __future__ import annotations

import os

import streamlit as st

from upload_routes import routes


app = st.App(
    "app.py",
    routes=routes(),
)


if __name__ == "__main__":
    app.run(
        config={
            "server.address": "0.0.0.0",
            # Codespaces/local default is 8501; Docker hosts (e.g. a
            # Hugging Face Space on 7860) override it with PORT.
            "server.port": int(os.environ.get("PORT", "8501")),
            "server.headless": True,
        }
    )
