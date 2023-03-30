
import time
import json
import asyncio
from typing import Iterable, Any
from aiohttp import ClientSession

import redis
from loguru import logger

from config import APP_CONFIG
from app.db.base import Base
from app.db.session import get_db
from app.model.event import Event
from app.model.webhook import Webhook
from app.model.webhook_to_event import WebhookToEvent
from app.utils.utils import get_table_by_name
from app.constant.base import (
    EVENT_CONDITION,
    WEBHOOK_EVENT_OPERATE, 
    REPACS_TIMEOUT, 
)


sess = next(get_db())
events: Iterable[Event] = None
rc = redis.Redis(
    host=APP_CONFIG["redis"]["host"],
    port=APP_CONFIG["redis"]["port"],
    db=APP_CONFIG["redis"]["wal_db"],
    decode_responses=True
)
RC_WAL_QUEUE = APP_CONFIG["redis"]["wal_queue"]

logger.add(
    "logs/webhook_subscriber_v0{time:YYYY-MM-DD}.log", 
    filter=lambda x: "webhook-sub_v0" in x["message"],
    rotation="00:00",
    retention="3 days",
)
logger.info("############ webhook-sub_v0 start ##############")

################# 兼容层开始 #######################################


def get_events() -> list:
    """ 获取所有的 event

    以下两种情况该函数会被调用：
    1. 第一次启动该脚本的时候
    2. 当接收到 event 的 wal 日志流的时候获取，更新缓存
    """
    events = [i for i in sess.query(Event).filter(Event.is_delete == False).yield_per(100)]
    logger.info("#### webhook-sub_v0 Current Event IDs: {0}".format([i.id for i in events]))
    return events


def is_shot_event(event: Event, wal: dict, table: Base) -> bool:
    if event.table != wal["table"]:
        return False
    if WEBHOOK_EVENT_OPERATE()[event.operate] != wal["kind"]:
        return False
    if event.shot_condition:
        tmp_wal = dict(zip(wal["columnnames"], wal["columnvalues"]))
        if WEBHOOK_EVENT_OPERATE()[event.operate] == "update":
            upt_filter = event.shot_condition.get("$upt_filter", {})
            old_tmp_wal = dict(zip(wal["oldkeys"]["keynames"], wal["oldkeys"]["keyvalues"]))
            upt_wal = {oldkey: tmp_wal[oldkey] for oldkey, oldvalue in old_tmp_wal.items() if oldvalue != tmp_wal[oldkey]}
            # logger.info(f"#### webhook-sub_v0 Update WAL: {old_tmp_wal} - {tmp_wal} - {upt_wal}")
            return shot_condition_expr(upt_wal, upt_filter, table)
        return shot_condition_expr(tmp_wal, event.shot_condition, table)
    return True


def shot_condition_expr(wal: dict, shot_condition: Any, table: Base) -> bool:
    """ 根据 shot_condition 判断 wal 是否满足命中条件

        !: 修改该方法需通过所有测试用例
        项目根目录下执行 `python -m unittest tests.test_event_shot_condition`
    """
    if not wal:
        return False

    expr = True
    for field, value in shot_condition.items():
        if wal.get(field) is None:
            expr = False
            continue
        
        if isinstance(value, list):
            expr = (
                # {"status": [1, 2]}
                expr and wal[field] in value
                if value and type(value[0]) in EVENT_CONDITION.BASE_TYPE else
                # {"$or": [{expr1, expr2...}]}
                expr and EVENT_CONDITION.LOGIC_FUNC_MAP[field](
                    (shot_condition_expr(wal, sc, table) for sc in value)
                )
            )
        # {"data": None} data 字段一旦发生变化就认为命中
        elif value is None:
            expr = True
        # {"status": 1}
        elif type(value) in EVENT_CONDITION.BASE_TYPE:
            expr = expr and getattr(wal[field], EVENT_CONDITION.EQ_OPERATOR)(value)
        # {"priority": {"$gt": 100, "lte": 600}}
        elif set(value.keys()) & set(EVENT_CONDITION.COMPARE_FUNC_MAP.keys()):
            for operator, v in value.items():
                expr = expr and getattr(wal[field], EVENT_CONDITION.COMPARE_FUNC_MAP[operator])(v)

    return expr


async def req(url: str, data: dict, ename: str) -> tuple:
    url = url.format(**APP_CONFIG)
    async with ClientSession() as session:
        # 这一步还需要验证一下
        try:
            async with session.post(url, json=data, timeout=REPACS_TIMEOUT) as resp:
                resp.raise_for_status()
                data = await resp.text()
        except Exception:
            return __import__('traceback').format_exc(), url, ename, False
        else:
            return data, url, ename, True


def callback_hook(events: Event, wal: dict) -> list:
    """ 回调函数触发
    """
    task_list = []
    for event in events:
        expr = (
            sess.query(
                WebhookToEvent.hook_pk,
                Webhook.url.label("url"),
                Webhook.payload.label("payload")
            )
            .join(Event, Event.id == WebhookToEvent.event_pk)
            .join(Webhook, Webhook.id == WebhookToEvent.hook_pk)
            .filter(
                WebhookToEvent.event_pk == event.id,
                WebhookToEvent.is_delete == False,
                Webhook.is_delete == False,
                Event.is_delete == False
            )
        )

        for hook in expr.yield_per(100):
            tmp_wal = dict(zip(wal["columnnames"], wal["columnvalues"]))            
            body = {field: tmp_wal.get(field) for field in hook.payload}
            # logger.info(f"#### webhook-sub_v0 Callback Req Body: {body}")
            req_async = req(hook.url, body, event.name)
            req_async_task = asyncio.create_task(req_async)
            task_list.append(req_async_task)

    return task_list


async def sub():
    global events
    events = get_events()

    while True:
        try:
            data = rc.lrange(RC_WAL_QUEUE, 0, 5)
            rc.ltrim(RC_WAL_QUEUE, 5, -1)
            if not data:
                time.sleep(0.1)
            else:
                for trasaction in data:
                    for wal in json.loads(trasaction)["change"]:
                        if wal["table"] == "event":
                            events = get_events()
                        else:
                            table = get_table_by_name(wal["table"])
                            # logger.info(f"#### webhook-sub_v0 Receive WalLog: {wal}")
                            shoting_events = [event for event in events if is_shot_event(event, wal, table)]
                            logger.info("#### webhook-sub_v0 Shoting EventIDs: {0}".format([i.id for i in shoting_events]))
                            task_list = callback_hook(shoting_events, wal)
                            try:
                                done, _ = await asyncio.wait(task_list, timeout=None)
                                logger.info("#### webhook-sub_v0 DoneCallback: {0}".format(
                                    "\n\n".join(["CallbackURL:{1} - CallBackRst:{3} - EventName:{2}".format(*i.result()) for i in done])
                                    )
                                )
                            except Exception:
                                if shoting_events:
                                    logger.info("#### webhook-sub_v0 ErrorCallback: {0}".format(__import__("traceback").format_exc()))
        except Exception:
            logger.info("#### webhook-sub_v0 GlobalError: {0}".format(__import__("traceback").format_exc()))



if __name__ == '__main__':
    logger.info("#### webhook-sub_v0 Start Wal Subscriber with V0 ...")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(sub())
