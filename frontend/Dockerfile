# ─── frontend — Dockerfile ─────────────────────────────────────────────────────
# Next.js standalone output — solo Node.js runtime, sin herramientas de build.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app

COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

ENV NODE_ENV=production
EXPOSE 3000

USER node

CMD ["node", "server.js"]
