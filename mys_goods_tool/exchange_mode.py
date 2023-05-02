import asyncio
import random
import sys
from datetime import datetime
from typing import Callable, Optional, TypeVar, Union, Tuple
from urllib.parse import urlparse

import ping3
from apscheduler.events import JobExecutionEvent, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from rich.console import RenderableType
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Event
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, ListView, ListItem

from mys_goods_tool.api import good_exchange, URL_EXCHANGE
from mys_goods_tool.custom_css import NONE
from mys_goods_tool.custom_widget import ControllableButton, UnClickableItem
from mys_goods_tool.data_model import ExchangeStatus
from mys_goods_tool.user_data import config as conf, ExchangePlan, Preference, ExchangeResult
from mys_goods_tool.utils import logger, LOG_FORMAT

# NtpTime.sync()

_T = TypeVar("_T")

ExchangeCallback = Callable[[ExchangeStatus, Optional[ExchangeResult]], None]
# AsyncExchangeCallback = Callable[[ExchangeStatus, Optional[ExchangeResult]], Coroutine[ExchangeStatus, Optional[
#     ExchangeResult], None]]
"""兑换回调函数类型"""


def _on_fail(status: ExchangeStatus, result: Optional[ExchangeResult]):
    if status.network_error:
        error = "网络错误"
    elif status.missing_stoken:
        error = "商品为游戏内物品，但 Cookies 缺少 stoken"
    elif status.missing_mid:
        error = "商品为游戏内物品，但 stoken 为 'v2' 类型同时 Cookies 缺少 mid"
    elif status.missing_address:
        error = "商品为实体物品，但未配置收货地址"
    elif status.missing_game_uid:
        error = "商品为游戏内物品，但未配置对应游戏的账号UID"
    elif status.unsupported_game:
        error = "暂不支持兑换对应分区/游戏的商品"
    elif status.failed_getting_game_record:
        error = "获取用户 GameRecord 失败"
    elif status.init_required:
        error = "未进行兑换任务初始化"
    elif status.account_not_found:
        error = "账号不存在"
    else:
        error = "未知错误"

    logger.error(f"用户 {result.plan.account.bbs_uid} - {result.plan.good.general_name} 兑换失败，原因：{error}")


def _get_api_host() -> Optional[str]:
    """
    获取商品兑换API服务器地址
    """
    hostname = urlparse(URL_EXCHANGE).hostname
    return str(hostname) if hostname else None


def _connection_test():
    """
    连接测试
    """
    hostname = _get_api_host()
    if not hostname:
        logger.warning("商品兑换API服务器地址解析失败")
        return False
    result = ping3.ping(hostname, unit="ms")
    if result is None:
        logger.info(f"Ping 商品兑换API服务器 {hostname} 超时")
    elif result is False:
        logger.info(f"Ping 商品兑换API服务器 {hostname} 失败")
    else:
        logger.info(f"Ping 商品兑换API服务器 {hostname} 延迟 {round(result, 2)} ms")
    return result


def get_scheduler():
    """
    获取兑换计划调度器
    """
    scheduler = AsyncIOScheduler()
    scheduler.configure(timezone=conf.preference.timezone or Preference.timezone)

    if conf.preference.enable_connection_test:
        interval = conf.preference.connection_test_interval or Preference.connection_test_interval
        scheduler.add_job(_connection_test, "interval", seconds=interval)

    for plan in conf.exchange_plans:
        scheduler.add_job(exchange_begin,
                          "date",
                          args=[plan],
                          run_date=datetime.fromtimestamp(plan.good.time)
                          )

    return scheduler


async def exchange_begin(plan: ExchangePlan):
    """
    到点后执行兑换

    :param plan: 兑换计划
    """
    logger.info(f"{plan.good.general_name} - {plan.good.time_text} 定时器触发，开始兑换")
    random_x, random_y = conf.preference.exchange_latency
    latency = random.uniform(random_x, random_y)
    await asyncio.sleep(latency)
    result = await good_exchange(plan)
    logger.info(f"{plan.good.general_name} - {plan.good.time_text} 兑换请求已发送")
    return result


def exchange_mode_simple():
    """
    普通文本模式（无Textual）
    """
    logger.add(sys.stdout, diagnose=True, format=LOG_FORMAT, level="DEBUG")
    if not conf.exchange_plans:
        logger.info("无兑换计划需要执行")
        return

    scheduler = get_scheduler()

    @lambda func: scheduler.add_listener(func, EVENT_JOB_EXECUTED)
    def on_executed(event: JobExecutionEvent):
        """
        接收兑换结果
        """
        result: Tuple[ExchangeStatus, Optional[ExchangeResult]] = event.retval
        exchange_status, exchange_result = result
        if exchange_result:
            logger.info(
                f"用户 {exchange_result.plan.account.bbs_uid} - {exchange_result.plan.good.general_name} 兑换成功")
        else:
            logger.error(
                f"用户 {exchange_result.plan.account.bbs_uid} - {exchange_result.plan.good.general_name} 兑换失败")

    try:
        scheduler.start()
        logger.info("兑换计划定时器已启动")
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("兑换计划定时器已停止")


class EnterExchangeMode(Event):
    """
    进入兑换模式的事件
    """
    pass


class ExitExchangeMode(Event):
    """
    退出兑换模式的事件
    """
    pass


class ExchangeModeWarning(Static):
    """
    进入/退出 兑换模式的提示文本
    """
    DEFAULT_CSS = """
    ExchangeModeWarning {
        width: 3fr;
    }
    """
    ENTER_TEXT = "确定要[bold]进入[/]兑换模式？进入兑换模式后[bold]无法使用其他功能[/]，定时兑换任务将会启动。你随时都可以退出，但定时任务将会停止。"
    EXIT_TEXT = "已进入兑换模式，你可以随时[bold]退出[/]。退出后[bold]定时兑换任务将会停止[/]。"
    display_text = reactive(ENTER_TEXT)

    def render(self) -> RenderableType:
        return self.display_text




class ExchangeModeView(Container):
    """
    兑换模式视图
    """
    DEFAULT_CSS = """
    ExchangeModeView {
        height: auto;
        width: 1fr;
        border: round #666;
        padding: 1;
        margin: 1 0;
    }
    
    ExchangeModeView ControllableButton {
        margin: 0 1;
        width: 1fr;
    }
    
    ExchangeModeView Horizontal {
        padding: 1;
        border: round #666;
    }
    
    ExchangeModeView ListView {
        overflow: hidden;
        height: auto;
    }
    """

    button_enter = ControllableButton("确定", variant="warning", id="button-exchange_mode-enter")
    button_exit = ControllableButton("退出", variant="error", id="button-exchange_mode-exit")
    button_refresh = ControllableButton("刷新", id="button-exchange_mode-refresh")
    button_exit.hide()
    warning_text = ExchangeModeWarning()
    scheduler = get_scheduler()
    """兑换计划调度器"""
    empty_data_item = ListItem(Static("暂无兑换计划，你可以尝试刷新"))
    list_view = ListView(empty_data_item)
    """兑换计划列表"""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self.warning_text
            yield self.button_enter
            yield self.button_exit
            yield self.button_refresh
        yield Static()
        yield ExchangeModePing()
        yield self.list_view

    async def update_data(self):
        """
        更新兑换计划列表
        """
        await self.list_view.clear()
        for plan in conf.exchange_plans:
            await self.list_view.append(ExchangeResultRow(plan))
        if not conf.exchange_plans:
            await self.list_view.append(self.empty_data_item)

    async def _on_button_pressed(self, event: ControllableButton.Pressed):
        if event.button.id == "button-exchange_mode-enter":
            self.button_refresh.disable()
            self.button_enter.hide()
            self.button_exit.show()
            self.warning_text.display_text = self.warning_text.EXIT_TEXT
            self.post_message(EnterExchangeMode())

        elif event.button.id == "button-exchange_mode-exit":
            self.button_refresh.enable()
            self.button_exit.hide()
            self.button_enter.show()
            self.warning_text.display_text = self.warning_text.ENTER_TEXT
            self.post_message(ExitExchangeMode())

        elif event.button.id == "button-exchange_mode-refresh":
            await self.update_data()

    async def _on_mount(self, event: events.Mount) -> None:
        await self.update_data()


class ExchangeResultRow(UnClickableItem):
    """
    兑换结果行
    """
    DEFAULT_CSS = """
    ExchangeResultRow {
        border: round #666;
        padding: 1;
        height: auto;
        width: 1fr;
        layout: horizontal;
    }
    
    ExchangeResultRow Container {
        width: 1fr;
        height: auto;
    }
    """
    def __init__(self, plan: ExchangePlan):
        """
        :param plan: 兑换计划
        """
        super().__init__()
        self.plan = plan
        """兑换计划"""
        self.result_preview = Container(self.get_result_static("等待兑换"))
        """兑换结果字样预览"""

    @classmethod
    def get_result_static(cls, text: str):
        """
        获取一个带有边框的Static 用于显示兑换结果
        """
        static = Static(text)
        static.styles.border = "round", "#666"
        static.styles.width = "1fr"
        return static

    def compose(self) -> ComposeResult:
        static = Static(f"[list]"
                   f"\n👓 米游社账号 - [bold green]{self.plan.account.bbs_uid}[/]"
                   f"\n📦 商品名称 - [bold green]{self.plan.good.goods_name}[/]"
                   f"\n📅 兑换时间 - [bold green]{self.plan.good.time_text}[/]"
                   f"\n🎮 游戏UID - [bold green]{self.plan.game_record.game_role_id if self.plan.game_record is not None else '[yellow]无需设置[/]'}[/]"
                   f"\n📮 收货地址 - [bold green]{self.plan.address.addr_ext if self.plan.address is not None else '[yellow]无需设置[/]'}[/]"
                   f"\n[/list]")
        static.styles.width = "2fr"
        yield static
        yield self.result_preview

    @lambda func: ExchangeModeView.scheduler.add_listener(func, EVENT_JOB_EXECUTED)
    def on_executed(self, event: JobExecutionEvent):
        """
        接收兑换结果
        """
        result: Tuple[ExchangeStatus, Optional[ExchangeResult]] = event.retval
        exchange_status, exchange_result = result
        if exchange_result:
            logger.info(
                f"用户 {exchange_result.plan.account.bbs_uid} - {exchange_result.plan.good.general_name} 兑换成功")
            static = self.get_result_static(f"[bold green]🎉 兑换成功[/]")
        else:
            logger.error(
                f"用户 {exchange_result.plan.account.bbs_uid} - {exchange_result.plan.good.general_name} 兑换失败")
            static = self.get_result_static(f"[bold red]💦 兑换失败[/]")
        self.result_preview.display = NONE
        self.mount(static)


class ExchangeModePing(Static):
    """
    兑换模式 Ping 结果的文本
    """
    DEFAULT_CSS = """
    ExchangeModePing {
        margin: 1 0;
    }
    """
    DEFAULT_VALUE = False
    ping_value: reactive[Union[float, bool, None]] = reactive(DEFAULT_VALUE)
    scheduler = get_scheduler()

    def render(self) -> RenderableType:
        return f"⚡ Ping | 商品兑换API服务器 [yellow]{_get_api_host() or 'N/A'}[/] - 延迟 [bold green]{self.ping_value or 'N/A'}[/] ms"

    @lambda func: ExchangeModeView.scheduler.add_listener(func, EVENT_JOB_EXECUTED)
    def update_ping(self, event: JobExecutionEvent):
        """
        更新 Ping 值
        """
        self.ping_value = event.retval
