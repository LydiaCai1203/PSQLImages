import time

import redis
from loguru import logger

from app.db.session import engine
from config import APP_CONFIG

rc = redis.Redis(
    host=APP_CONFIG["redis"]["host"],
    port=APP_CONFIG["redis"]["port"],
    db=APP_CONFIG["redis"]["wal_db"],
    decode_responses=True
)
RC_WAL_QUEUE = APP_CONFIG["redis"]["wal_queue"]
RC_WAL_THRESHOLD = int(APP_CONFIG["redis"]["wal_threshold"])

logger.add(
    "logs/webhook_publisher{time:YYYY-MM-DD}.log", 
    filter=lambda x: "webhook-pub" in x["message"],
    rotation="00:00",
    retention="3 days",
)

logger.info("############ webhook-pub start ##############")

############################################################################

def over_threshold() -> bool:
    # TODO 超过某一阈值进行钉钉告警
    return rc.llen(RC_WAL_QUEUE) > RC_WAL_THRESHOLD


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
        for row in rst:
            time.sleep(0.01)
            logger.info(f'### webhook-pub publish data: {row["data"]} ###')
            rc.rpush(RC_WAL_QUEUE, row["data"])
            num += 1


if __name__ == '__main__':
    logger.info("#### webhook-pub Start Wal Publisher ...")
    while True:
        if not over_threshold() and not pub():
            time.sleep(1)
