FROM node:24-alpine AS dependencies

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

FROM dependencies AS development

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM dependencies AS build

RUN npm run build

FROM nginx:1.29-alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

ENV HARBOR_API_PROXY_TARGET=http://harbor-api:8411

EXPOSE 8080
