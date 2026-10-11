# QueryMind frontend (design §14.1). Build context: frontend/.
FROM node:22-alpine AS build
WORKDIR /src

# Install dependencies first so source changes don't re-download them.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# Static files behind nginx, as a non-root user on port 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy_params.conf /etc/nginx/proxy_params.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
