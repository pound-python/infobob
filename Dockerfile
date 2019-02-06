FROM pypy:2

WORKDIR /usr/src/app
COPY current-requirements.txt ./
RUN pip wheel --no-cache-dir -r current-requirements.txt -w wheelhouse
COPY . ./src/
RUN pip wheel --no-cache-dir --no-deps ./src -w wheelhouse

FROM pypy:2-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 sqlite3 \
 && rm -rf /var/lib/apt/lists/*
RUN adduser --system --group --home /usr/src/app --disabled-login infobob

WORKDIR /usr/src/app
COPY --from=0 /usr/src/app/wheelhouse wheelhouse
RUN pip install ./wheelhouse/*

COPY infobob.cfg.example db.schema ./
RUN mkdir -p /app/db
RUN sqlite3 <db.schema /app/db/infobob.sqlite
RUN chown -R infobob: /app
VOLUME /app
USER infobob
ENTRYPOINT ["twistd", "--pidfile=", "-n", "infobob"]
CMD ["infobob.cfg.example"]
