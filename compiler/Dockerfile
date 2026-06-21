# Mycelium compiler — machine-independent image.
#
# Toolchain: rustc 1.95, stellar-cli 27.0.0 (latest stable), target wasm32v1-none,
# soroban-sdk 26.1.0. Changing any of these can change which contracts compile, so the
# 129-contract baseline is re-verified against this image (see scripts/compile_batch.py)
# and these versions are kept in sync with compiler/mycelium_compiler/codegen/core.py.
FROM rust:1.95-slim-bookworm

# Limit Cargo to a single job/thread to keep peak memory low on small machines.
ENV CARGO_BUILD_JOBS=1

# stellar-cli version — keep in sync with ensure_stellar_cli() in core.py.
ARG STELLAR_VERSION=27.0.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    python3 \
    # Runtime libs the stellar-cli 25.x binary is dynamically linked against
    # (absent from slim-bookworm; missing them makes `stellar` fail with exit 127).
    libdbus-1-3 \
    libudev1 \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# WASM targets. stellar-cli 25.x builds for wasm32v1-none; keep
# wasm32-unknown-unknown as a fallback for older SDKs.
RUN rustup target add wasm32v1-none wasm32-unknown-unknown

# Install the stellar-cli binary (pinned).
RUN curl -fL -o /tmp/stellar.tar.gz \
    https://github.com/stellar/stellar-cli/releases/download/v${STELLAR_VERSION}/stellar-cli-${STELLAR_VERSION}-x86_64-unknown-linux-gnu.tar.gz \
    && tar -xzf /tmp/stellar.tar.gz -C /usr/local/bin \
    && rm /tmp/stellar.tar.gz \
    && stellar --version

# Pre-warm the cargo registry + the static workspace that generate_wasm reuses.
# Building the dummy lib for wasm32v1-none caches soroban-sdk and all transitive
# deps so later compiles run fully offline (CARGO_NET_OFFLINE=true in core.py).
WORKDIR /app/mycelium_contract_workspace
RUN mkdir -p src \
    && printf '#![no_std]\nuse soroban_sdk::{contract, contractimpl, Env};\n#[contract]\npub struct Contract;\n#[contractimpl]\nimpl Contract { pub fn hello(_env: Env) {} }\n' > src/lib.rs \
    && printf '[package]\nname = "mycelium_contract"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\ncrate-type = ["cdylib"]\n\n[dependencies]\nsoroban-sdk = "26.1.0"\n\n[profile.release]\nopt-level = "z"\noverflow-checks = true\nlto = true\ncodegen-units = 1\npanic = "abort"\n' > Cargo.toml \
    && CARGO_TARGET_DIR=/app/cargo_target stellar contract build --manifest-path Cargo.toml

# Copy the compiler + SDK sources.
WORKDIR /app
COPY compiler/ /app/compiler/
COPY mycelium/ /app/mycelium/
COPY sdk/ /app/sdk/

ENV PYTHONPATH="/app/compiler:/app/sdk:."

# Default: compile a single contract mounted at /workspace/contract.py.
# Override the entrypoint to run scripts/compile_batch.py for bulk builds.
ENTRYPOINT ["python3", "-m", "mycelium_compiler.main", "/workspace/contract.py", "-o", "/workspace/target.wasm"]
