# -------------------
# BUILD STAGE
# -------------------
FROM rust:latest as builder

RUN apt update && apt install -y \
    pkg-config \
    libssl-dev \
    libpq-dev

WORKDIR /app

COPY . .

# Force SQLx offline
ENV SQLX_OFFLINE=true

RUN cargo build --release

# -------------------
# RUNTIME STAGE
# -------------------
FROM ubuntu:22.04

RUN apt update && apt install -y \
    ca-certificates \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/target/release/etsi_gs_qkd_014_referenceimplementation .

ENTRYPOINT ["/app/etsi_gs_qkd_014_referenceimplementation"]