FROM node:lts-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .

ARG DESKTOP_API_URL=http://localhost:8000
ARG PHONE_API_URL=http://localhost:8000
ENV DESKTOP_API_URL=$DESKTOP_API_URL \
    PHONE_API_URL=$PHONE_API_URL

RUN npm run build

FROM node:lts-alpine

ENV NODE_ENV=production
WORKDIR /app

COPY --from=build /app/package*.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY --from=build /app/build ./build

EXPOSE 6868
USER node
CMD ["npm", "start"]
