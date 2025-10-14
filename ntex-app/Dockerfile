# syntax=docker/dockerfile:1
FROM rust:1.83 as builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
RUN useradd --system --create-home --home /app app
WORKDIR /app
COPY --from=builder /app/target/release/ntex-app /usr/local/bin/ntex-app
ENV RUST_LOG=info
USER app
EXPOSE 8080
CMD ["ntex-app"]
