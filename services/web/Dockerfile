# syntax=docker/dockerfile:1
FROM nginx:1.27-alpine

COPY services/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY services/web/index.html services/web/app.js services/web/style.css /usr/share/nginx/html/
COPY services/web/img/ /usr/share/nginx/html/img/
COPY services/web/fonts/ /usr/share/nginx/html/fonts/
# Права на чтение для nginx (файлы с Mac могут прийти с правами 600)
RUN chmod -R a+rX /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
