"""
面部特征识别测试脚本
"""

import sys
import os
from unittest.mock import Mock, MagicMock
from dataclasses import dataclass

# 添加项目根目录和 agent 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent.action.zshg.role_utils import extract_facial_features, Feature


@dataclass
class MockRecognitionResult:
    """模拟识别结果"""

    hit: bool
    box: list
    best_result: object
    all_results: list


@dataclass
class MockOCRResult:
    """模拟 OCR 结果"""

    text: str
    score: float
    box: list


def test_facial_features():
    """
    测试面部特征识别
    """
    # 创建模拟上下文
    mock_context = Mock()

    # 模拟控制器和截图
    mock_controller = Mock()
    mock_screencap = Mock()
    mock_screencap.wait.return_value.get.return_value = "mock_screencap"
    mock_controller.post_screencap.return_value = mock_screencap

    mock_tasker = Mock()
    mock_tasker.controller = mock_controller
    mock_context.tasker = mock_tasker

    # 模拟面部特征识别结果
    def mock_run_recognition(name, *args, **kwargs):
        if name == "FacialFeature_AncientSpiritEars":
            # 模拟识别到上古灵性耳朵
            return MockRecognitionResult(
                hit=True, box=[100, 200, 50, 50], best_result=None, all_results=[]
            )
        elif name == "FacialFeature_FocusedEyes":
            # 模拟识别到专注之瞳
            return MockRecognitionResult(
                hit=True, box=[250, 250, 30, 30], best_result=None, all_results=[]
            )
        elif name == "FacialFeature_NaturalEmpathyBrows":
            # 模拟识别到自然共感眉毛
            return MockRecognitionResult(
                hit=True, box=[220, 220, 40, 20], best_result=None, all_results=[]
            )
        return MockRecognitionResult(
            hit=False, box=[], best_result=None, all_results=[]
        )

    mock_context.run_recognition = mock_run_recognition

    # 测试面部特征识别
    features = extract_facial_features(mock_context)

    # 验证结果
    print("测试面部特征识别结果:")
    print(f"识别到的特性数量: {len(features)}")
    for feature in features:
        print(f"- {feature.name}")

    # 验证是否识别到了所有预期的特性
    expected_features = ["上古灵性", "专注之瞳", "自然共感"]
    actual_feature_names = [f.name for f in features]

    for expected in expected_features:
        assert expected in actual_feature_names, f"未识别到预期特性: {expected}"

    print("\n测试通过！所有预期的面部特征都被成功识别。")


if __name__ == "__main__":
    test_facial_features()
