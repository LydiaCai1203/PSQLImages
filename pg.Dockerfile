FROM postgres:14.2

RUN export http_proxy=http://10.0.50.10:7890 && export https_proxy=http://10.0.50.10:7890 && \
    export PATH=/usr/lib/postgresql/14/bin/:$PATH && \
    apt-get update && \
    apt-get -y install postgresql-server-dev-14 && \
    apt-get install postgresql-14-wal2json

RUN set eux; \
	confdir='/docker-postgres-conf.d/'; \
	mkdir -p "$confdir"; \
	chown postgres:postgres "$confdir"; \
	echo "include_dir = '$confdir'" >> /usr/share/postgresql/postgresql.conf.sample