FROM node:22-alpine

WORKDIR /app/frontend

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

RUN corepack enable && corepack prepare pnpm@10.33.0 --activate

COPY frontend/package.json ./
COPY frontend/pnpm-lock.yaml ./
COPY frontend/.npmrc ./
COPY frontend/tsconfig.json ./
COPY frontend/next-env.d.ts ./
COPY frontend/next.config.mjs ./
COPY frontend/postcss.config.cjs ./
COPY frontend/tailwind.config.ts ./
RUN pnpm install --frozen-lockfile

COPY frontend ./ 

RUN pnpm run build

EXPOSE 3000

CMD ["pnpm", "run", "start"]
