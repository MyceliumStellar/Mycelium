FROM rust:1.88-slim-bookworm

# Limit Cargo to compile using only 1 job/thread to prevent memory exhaustion on 4GB devices
ENV CARGO_BUILD_JOBS=1

# Install python3 and system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Add WASM compilation target
RUN rustup target add wasm32-unknown-unknown

# Download and install stellar-cli binary (v22.0.1)
RUN curl -L -o /tmp/stellar.tar.gz https://github.com/stellar/stellar-cli/releases/download/v22.0.1/stellar-cli-22.0.1-x86_64-unknown-linux-gnu.tar.gz \
    && tar -xzf /tmp/stellar.tar.gz -C /usr/local/bin \
    && rm /tmp/stellar.tar.gz

# Pre-warm cargo registry by compiling a dummy cargo project with soroban-sdk
# We use the job limit to ensure low memory consumption during this step.
WORKDIR /app/mycelium_contract_workspace
RUN cargo init --lib --name mycelium_contract \
    && printf '[package]\nname = "mycelium_contract"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\ncrate-type = ["cdylib"]\n\n[dependencies]\nsoroban-sdk = "22.0.0"\n\n[profile.release]\nopt-level = "z"\noverflow-checks = true\nlto = true\ncodegen-units = 1\npanic = "abort"\n' > Cargo.toml \
    && CARGO_TARGET_DIR=/app/cargo_target stellar contract build --manifest-path Cargo.toml

# Copy compiler source code and SDK files into the image
WORKDIR /app
COPY compiler/ /app/compiler/
COPY mycelium/ /app/mycelium/
COPY sdk/ /app/sdk/

ENV PYTHONPATH="/app/compiler:/app/sdk:."

# Entrypoint runs the compiler command inside /workspace/
ENTRYPOINT ["python3", "-m", "mycelium_compiler.main", "/workspace/contract.py", "-o", "/workspace/target.wasm"]
