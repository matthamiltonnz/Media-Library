ARG BUILD_FROM
ARG BUILD_VERSION
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/media.db \
    PORT=8099 \
    ADDON_VERSION=${BUILD_VERSION}

# python3 serves the app, sqlite is needed by migrate.sh
RUN apk add --no-cache python3 sqlite

WORKDIR /app
COPY app/        /app/
COPY config.yaml /app/addon-config.yaml
COPY migrations/ /app/migrations/
COPY migrate.sh  /app/migrate.sh
COPY run.sh      /run.sh
RUN chmod a+x /app/migrate.sh /run.sh

CMD [ "/run.sh" ]
