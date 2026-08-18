# ---------------------------------------------------------------- frontend
# Build em Node, serve em nginx: a imagem final não carrega node_modules
# (~200MB) nem o runtime do Node. Fica em ~50MB.
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` sem fallback: se o package-lock.json estiver fora de sincronia, o
# build tem que falhar em vez de instalar versões diferentes das testadas.
RUN npm ci

COPY . .

# Os testes rodam ANTES do build: um componente quebrado derruba a imagem em
# vez de virar bug em produção.
RUN npm test

RUN npm run build

# ---------------------------------------------------------------- runtime
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
