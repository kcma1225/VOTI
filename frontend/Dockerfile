# syntax=docker/dockerfile:1
# Build context: repository root (see compose.yaml → nginx.build). Ignore rules: frontend/Dockerfile.dockerignore.

# ─── build the SPA ───
FROM node:24.21.0-alpine3.24 AS build
WORKDIR /app
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    npm_config_fund=false \
    npm_config_audit=false
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ─── serve with unprivileged nginx on :8080 ───
FROM nginxinc/nginx-unprivileged:1.31.6-alpine3.24
COPY --chown=nginx:nginx frontend/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx frontend/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html
EXPOSE 8080
