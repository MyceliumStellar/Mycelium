FROM rust:1.95-slim-bookworm
ENV CARGO_BUILD_JOBS=1
ARG STELLAR_VERSION=27.0.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates python3 python3-pip python3-venv \
    libdbus-1-3 libudev1 libssl3 \
    && rm -rf /var/lib/apt/lists/*
RUN rustup target add wasm32v1-none wasm32-unknown-unknown
RUN curl -fL -o /tmp/stellar.tar.gz \
    https://github.com/stellar/stellar-cli/releases/download/v${STELLAR_VERSION}/stellar-cli-${STELLAR_VERSION}-x86_64-unknown-linux-gnu.tar.gz \
    && tar -xzf /tmp/stellar.tar.gz -C /usr/local/bin \
    && rm /tmp/stellar.tar.gz
# Pre-warm cargo registry for offline compilation
WORKDIR /app/mycelium_contract_workspace
RUN mkdir -p src \
    && printf '#![no_std]\nuse soroban_sdk::{contract, contractimpl, Env};\n#[contract]\npub struct Contract;\n#[contractimpl]\nimpl Contract { pub fn hello(_env: Env) {} }\n' > src/lib.rs \
    && printf '[package]\nname = "mycelium_contract"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\ncrate-type = ["cdylib"]\n\n[dependencies]\nsoroban-sdk = "26.1.0"\n\n[profile.release]\nopt-level = "z"\noverflow-checks = true\nlto = true\ncodegen-units = 1\npanic = "abort"\n' > Cargo.toml \
    && CARGO_TARGET_DIR=/app/cargo_target stellar contract build --manifest-path Cargo.toml
WORKDIR /app
COPY . /app
# Setup virtual environment and install backend requirements
RUN python3 -m venv /app/venv
RUN /app/venv/bin/pip install --upgrade pip
RUN /app/venv/bin/pip install -r ide/backend/requirements.txt
RUN /app/venv/bin/pip install -r requirements.txt
ENV PYTHONPATH="/app/compiler:/app/sdk:/app:/app/ide/backend"
EXPOSE 8000
CMD ["/app/venv/bin/uvicorn", "ide.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
