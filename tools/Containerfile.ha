FROM ghcr.io/home-assistant/home-assistant:2026.11.1
RUN python3 -m venv --system-site-packages /opt/matter-reflex-tests \
    && /opt/matter-reflex-tests/bin/python -m pip install --no-cache-dir \
        pytest==9.0.3 pytest-asyncio==1.4.0 ruff==0.17.3
ENV PATH="/opt/matter-reflex-tests/bin:${PATH}"
