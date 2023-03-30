# postgresql + 自定义功能的镜像整理


[官方 GitHub](https://github.com/docker-library/postgres)
```markdown
官方提供的 Docker Image 不支持自定义的 postgresql.conf 的配置

postgresql.conf 里的 include_dir 支持对 postgresql.conf 进行配置项的补充，因此重封镜像，创建补充配置目录，并将容器外的配置文件映射进来。详情见issues-835，这也是官方提供的做法。
```

## 1. 带有 WaLog 日志

**1. 解析日志的工具**
[wal2json](https://github.com/eulerto/wal2json)

**2. 进入 postgresql 容器 && 安装 wal2json**
```bash
export http_proxy=http://{host}:{port} && \
export https_proxy=http://{host}:{port} && \
export PATH=/usr/lib/postgresql/14/bin/:$PATH && \
apt-get update && \
apt-get -y install postgresql-server-dev-14 && \
apt-get install postgresql-14-wal2json
```

**3. 替换配置文件**
```bash
docker cp docker/pg_hba.conf warhead.repacs.postgres:/data/postgres/pgdata
docker cp docker/postgresql.conf warhead.repacs.postgres:/data/postgres/pgdata
```

**4. 创建 slot**
```bash
pg_recvlogical -U pacs -d pacs --slot pacs_slot --create-slot -P wal2json
```
```python
def create_slot() -> tuple:
    pg_config = APP_CONFIG["postgres"]
    redis_config = APP_CONFIG["redis"]
    os.environ['PGPASSWORD'] = pg_config['password']

    text = os.popen(
        "pg_recvlogical "
        f"--host={pg_config['host']} "
        f"--username={pg_config['username']} -w "
        f"--dbname={pg_config['database']} "
        f"--slot={redis_config['wal_queue']} --create-slot -P wal2json"
    )
    rst = text.read()
    return (True, "") if not rst or "exist" in rst else (False, rst)
```

**5. 提交变更部分创建新镜像**
```bash
docker commit -a caiqj -m "add wal2json plugin" warhead.repacs.postgres hub.infervision.com/vendor/postgres:14.2_wal2json
```

**6. 终端监听**
```bash
pg_recvlogical -U pacs -d pacs --slot pacs_slot --start -o pretty-print=1 -o add-msg-prefixes=wal2json -f -
```

**7. 脚本监听**
```python
# 设置表的 REPLICA IDENTITY，否则更新事件会出错 - 只给需要的表加上
def update_replica_identity() -> bool:
    # 需要监听更新事件的表加上 REPLICA IDENTITY
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text("ALTER TABLE task REPLICA IDENTITY FULL;"))
            conn.execute(text("ALTER TABLE result REPLICA IDENTITY FULL;"))
            trans.commit()
        except Exception:
            trans.rollback()
            msg = traceback.format_exc()
            logger.error(msg)
            return False
        else:
            return True
        finally:
            trans.close()
```

```python
# 脚本查询对应的槽就可以获得 walog 数据
def pub():
    sql = """
        SELECT data
        FROM pg_logical_slot_get_changes(
            'pacs_slot', NULL, NULL, 'pretty-print', '1'
        )
        limit 100;
    """
    num = 0
    with engine.connect() as conn:
        rst = conn.execute(sql)
        print(rst)
```


## 2. 带有更新时间
