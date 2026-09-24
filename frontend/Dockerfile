FROM node:20-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_COMPANY_PROFILE_WORKER_ENABLED=false
ENV NEXT_PUBLIC_COMPANY_PROFILE_WORKER_ENABLED=${NEXT_PUBLIC_COMPANY_PROFILE_WORKER_ENABLED}
ARG BACKEND_URL=http://backend:8001
ENV BACKEND_URL=${BACKEND_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

COPY --from=builder /app/.next.nosync/standalone ./
COPY --from=builder /app/.next.nosync/static ./.next.nosync/static

EXPOSE 3000

CMD ["node", "server.js"]
