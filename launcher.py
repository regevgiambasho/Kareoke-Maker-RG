from __future__ import annotations

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
            "server.port": 8501,
            "server.headless": True,
        }
    )
