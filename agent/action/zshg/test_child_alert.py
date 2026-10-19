#!/usr/bin/env python3
"""
好苗子弹窗功能测试文件
"""

import sys
import os

# 模拟utils模块
class MockLogger:
    def info(self, msg):
        print(f"[INFO] {msg}")
    def warning(self, msg):
        print(f"[WARNING] {msg}")
    def error(self, msg):
        print(f"[ERROR] {msg}")

# 创建mock utils模块
class MockUtils:
    logger = MockLogger()

# 添加到sys.modules
sys.modules['utils'] = MockUtils()

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from agent.action.zshg.child import (
    evaluate_potential,
    evaluate_features,
    load_good_features
)
from agent.action.zshg.role_utils import Potential, Feature


def test_potential_evaluation():
    """
    测试潜力属性评估
    """
    print("=== 测试潜力属性评估 ===")
    
    # 测试用例1：3个S属性，应该返回True
    potential1 = Potential()
    potential1.values = {
        "力量": 0.8,  # S
        "体质": 0.85,  # S
        "技巧": 0.9,  # S
        "感知": 0.5,  # A
        "敏捷": 0.4,  # B
        "意志": 0.3  # C
    }
    result1, count1 = evaluate_potential(potential1)
    print(f"测试用例1（3个S属性）: {result1}, S个数: {count1}")
    assert result1 == True, "测试用例1失败"
    assert count1 == 3, "测试用例1 S个数计算错误"
    
    # 测试用例2：2个S属性，应该返回False
    potential2 = Potential()
    potential2.values = {
        "力量": 0.8,  # S
        "体质": 0.85,  # S
        "技巧": 0.6,  # A
        "感知": 0.5,  # A
        "敏捷": 0.4,  # B
        "意志": 0.3  # C
    }
    result2, count2 = evaluate_potential(potential2)
    print(f"测试用例2（2个S属性）: {result2}, S个数: {count2}")
    assert result2 == False, "测试用例2失败"
    assert count2 == 2, "测试用例2 S个数计算错误"
    
    # 测试用例3：包含SS属性
    potential3 = Potential()
    potential3.values = {
        "力量": 0.95,  # SS
        "体质": 0.9,  # S
        "技巧": 0.85,  # S
        "感知": 0.5,  # A
        "敏捷": 0.4,  # B
        "意志": 0.3  # C
    }
    result3, count3 = evaluate_potential(potential3)
    print(f"测试用例3（包含SS属性）: {result3}, S个数: {count3}")
    assert result3 == True, "测试用例3失败"
    assert count3 == 3, "测试用例3 S个数计算错误"
    
    print("潜力属性评估测试通过！\n")


def test_feature_evaluation():
    """
    测试特性评估
    """
    print("=== 测试特性评估 ===")
    
    # 加载好特性列表
    good_features_list = load_good_features()
    
    # 测试用例1：包含好特性
    features1 = [
        Feature(name="太阳之子", description="拥有太阳的力量"),
        Feature(name="普通特性", description="普通的特性")
    ]
    result1, good_features1 = evaluate_features(features1, good_features_list)
    print(f"测试用例1（包含好特性）: {result1}, 好特性: {good_features1}")
    assert result1 == True, "测试用例1失败"
    assert len(good_features1) > 0, "测试用例1好特性识别失败"
    
    # 测试用例2：不包含好特性
    features2 = [
        Feature(name="普通特性1", description="普通的特性1"),
        Feature(name="普通特性2", description="普通的特性2")
    ]
    result2, good_features2 = evaluate_features(features2, good_features_list)
    print(f"测试用例2（不包含好特性）: {result2}, 好特性: {good_features2}")
    assert result2 == False, "测试用例2失败"
    assert len(good_features2) == 0, "测试用例2好特性识别错误"
    
    # 测试用例3：包含多个好特性
    features3 = [
        Feature(name="太阳之子", description="拥有太阳的力量"),
        Feature(name="科内塔之怒", description="科内塔的愤怒"),
        Feature(name="普通特性", description="普通的特性")
    ]
    result3, good_features3 = evaluate_features(features3, good_features_list)
    print(f"测试用例3（包含多个好特性）: {result3}, 好特性: {good_features3}")
    assert result3 == True, "测试用例3失败"
    assert len(good_features3) >= 2, "测试用例3好特性识别不完整"
    
    print("特性评估测试通过！\n")


def test_load_good_features():
    """
    测试加载好特性配置
    """
    print("=== 测试加载好特性配置 ===")
    
    good_features = load_good_features()
    print(f"加载的好特性数量: {len(good_features)}")
    print(f"好特性列表: {good_features}")
    assert len(good_features) > 0, "好特性加载失败"
    
    print("好特性配置加载测试通过！\n")


def run_all_tests():
    """
    运行所有测试
    """
    print("开始运行好苗子弹窗功能测试...\n")
    
    try:
        test_load_good_features()
        test_potential_evaluation()
        test_feature_evaluation()
        print("🎉 所有测试通过！")
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
