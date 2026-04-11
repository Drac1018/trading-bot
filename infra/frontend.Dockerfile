FROM node:22-alpine

WORKDIR /app/frontend

COPY frontend/package.json ./
COPY frontend/tsconfig.json ./
COPY frontend/next-env.d.ts ./
COPY frontend/next.config.mjs ./
COPY frontend/postcss.config.js ./
COPY frontend/tailwind.config.ts ./
RUN npm install

COPY frontend ./ 

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
