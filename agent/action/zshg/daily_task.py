from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from utils import logger

import os
import cv2


@AgentServer.custom_action("DailyTaskProcessor")
class DailyTaskProcessor(CustomAction):
    """
    处理每日任务
    """

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        """
        处理每日任务
        """
        # 任务列表配置
        task_list = [
            "BigMapMarket",  # 市场折扣物品
            "BigMapMall",  # 商城免费礼包
            "BigMapRewardToken",  # 悬赏令领取
        ]

        # 遍历任务列表，执行启用的任务
        for task_entry in task_list:
            taskDetail = context.get_node_data(task_entry)
            if context.tasker.stopping:
                logger.info("任务执行被停止")
                break
            if not taskDetail.get("enabled"):
                continue

            logger.info(f"开始执行任务 {task_entry}")

            # 执行任务
            context.run_task(task_entry)
            logger.info(f"任务执行完成 {task_entry}")
            context.run_task("UI_ReturnBigMap")

        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("TestFunc")
class TestFunc(CustomAction):
    """
    测试函数
    """

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:

        # 测试用：载入本地图片（cv2 读取为 BGR，转 RGB）
        local_image_path = os.path.join("1.jpg")
        image = cv2.imread(local_image_path)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        context.run_recognition(
            "塞宁面部特征_太阳之子",
            image,
        )

        return CustomAction.RunResult(success=True)
