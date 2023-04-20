FROM postgres:14.2

RUN sed -i s/deb.debian.org/mirrors.aliyun.com/g /etc/apt/sources.list && \
    sed -i s/security.debian.org/mirrors.aliyun.com/g /etc/apt/sources.list

RUN export PATH=/usr/lib/postgresql/14/bin/:$PATH && \
    apt-get update && \
    apt-get -y install postgresql-server-dev-14 && \
    apt-get install postgresql-14-wal2json

COPY ./with_walog/postgresql.conf /docker-postgres-conf.d/postgresql.conf

RUN set eux; \
	confdir='/docker-postgres-conf.d/'; \
	mkdir -p "$confdir"; \
	chown postgres:postgres "$confdir"; \
	echo "include_dir = '$confdir'" >> /usr/share/postgresql/postgresql.conf.sample