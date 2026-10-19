from dataclasses import dataclass, field
import re


# ==================== 聊天看相模式数据结构 ====================
@dataclass
class MatchmakerMessage:
    """媒人话语单条"""

    round_num: int
    text: str
    timestamp: float


@dataclass
class ChatCandidateProfile:
    """聊天看相模式收集的完整信息"""

    name: str = ""
    country: str = ""
    title: str = ""  # 爵位
    age: int = 0
    bloodlines: dict[str, float] = field(default_factory=dict)  # 血统 {血统名: 百分比}
    detected_bloodline_trait: str = ""  # 三区血脉词条原文
    positive_traits: list[str] = field(default_factory=list)  # 四区优质特性（笑脸）
    negative_traits: list[str] = field(default_factory=list)  # 五区劣质特性（哭脸）
    chat_messages: list[MatchmakerMessage] = field(default_factory=list)
    total_score: float = 0.0
    has_orange_feature: bool = False  # 是否有橙色/金色面部特征（唯一接受标准）


class TraitConfig:
    """
    相亲词条配置

    基于游戏词条文档重构，支持以下区域：
    - 一区：喜好特性（忽略）
    - 二区：年龄特性（忽略，废词条）
    - 三区：血脉特性（仅记录）
    - 四区：优质特性（笑脸，核心评分）
    - 五区：劣质特性（哭脸，核心评分）
    - 六区：爵位特性（仅记录）
    """

    # ========== 三区：血脉特性（完整列表）==========
    BLOODLINE_PATTERNS = {
        "加尔提斯人": "加尔提斯",
        "一直居住在这片大陆": "加尔提斯",
        "塞尼斯特的贵族血统": "塞宁王族",
        "赫雷斯特的贵族血统": "希尔王族",
        "佩里亚诺的贵族血统": "佩尔弗因王族",
        "威弗提亚贵族通婚": "切瓦力王族",
        "瓦斯提亚的贵族血统": "弗莱德里王族",
        "北方海外": "古特人",
        "北地人的王族血统": "古特雅尔",
        "血统纯正的原居民": "法拉希尔血裔",
        "祖辈来自法拉希尔": "原居民",
        "纯正的法拉希尔血裔": "法拉希尔血裔",
        "东方森林国度": "原居民",
        "传说中的精灵血统": "高阶精灵",
        "操纵魔法的精灵血统": "高阶精灵",
        "南方海岸之外的大陆": "玛夏人",
        "明显的玛夏人特征": "玛夏人",
        "玛夏贵族的后裔": "玛夏贵族",
        "远方玛夏的贵族血统": "玛夏贵族",
        "远方草原来的": "萨尼德人",
        "无人的戈壁": "萨尼德人",
        "哈苏汉国的王族血统": "萨尼德罕",
        "远方荒漠的权贵": "萨尼德罕",
        "遥远的东方，优雅，贵族": "宏朝贵胄",
        "东方宏朝的贵族血统": "宏朝贵胄",
        "擅长奔跑的种族": "祖扎尔达人",
        "祖扎尔达人的血液": "祖扎尔达人",
        "祖扎尔达的王族": "祖扎尔达王族",
        "很遥远的地方，破灭的国度": "瓦诺遗族",
        "瓦诺人的血脉": "瓦诺遗族",
    }

    # ========== 四区：优质特性（笑脸）==========
    # 格式: "匹配词": "特性名"
    POSITIVE_TRAITS = {
        "精神上强烈不安": "神经质",
        "嗜血": "嗜血",
        "非常聪明": "聪明",
        "脑袋里面想的东西比别人多": "聪明",
        "笑容充满阳光": "微笑天使",
        "鼓舞他人": "微笑天使",
        "心灵手巧": "灵动",
        "动作矫健": "灵动",
        "魅力十足": "魅力",
        "吸引同龄人羡慕": "魅力",
        "学习能力很强": "勤奋好学",
        "自己也很努力": "勤奋好学",
        "浪漫": "浪漫",
        "暴力可以解决一切": "施暴者",
        "性情中人": "感性",
        "好奇心很重": "好奇",
        "不切实际": "应变",
        "干起活来从不嫌累": "铁肺",
        "力气很大": "臂力过人",
        "天性残忍": "残忍",
        "成熟稳重": "沉稳",
        "让人放心": "沉稳",
        "远视": "远视",
        "虔诚": "虔诚",
        "诸神庇护": "虔诚",
        "搔首弄姿": "卖弄风情",
        "十分不检点": "卖弄风情",
    }

    # ========== 五区：劣质特性（哭脸）==========
    # 格式: "匹配词": "特性名"
    NEGATIVE_TRAITS = {
        "耳朵感觉像是被堵住": "耳背",
        "告诉ta点事情要费好大的劲": "耳背",
        "木讷": "木讷",
        "呆呆": "木讷",
        "懒懒散散": "懒惰",
        "懒虫": "懒惰",
        "不择手段": "卑劣",
        "粗鲁无理": "粗鲁",
        "笨手笨脚": "笨手笨脚",
        "好色": "好色",
        "邋遢": "邋遢",
        "粗枝大叶": "粗枝大叶",
        "疯狂地迷恋自己": "自恋",
        "听不到任何声音": "耳聋",
        "拜金": "拜金主义",
        "总爱挖苦": "爱挖苦",
        "六个脚趾": "畸足",
        "非常抗拒与异性交往": "苦修",
        "干活时会没力气": "没力气",
        "挨打中寻求快感": "受苦者",
        "早产儿": "早产儿",
    }

    # ========== 六区：爵位特性 ==========
    TITLE_PATTERNS = {
        "祖辈是贵族，兄长受封骑士": "骑士",
        "战功卓越的老骑士": "骑士",
        "都会成为骑士": "骑士",
        "低调的贵族": "男爵",
        "祖孙三代战功赫赫": "男爵",
        "新兴的贵族家族，商业发展": "男爵",
        "丰厚基业，锦衣玉食": "伯爵",
        "娴熟外交技术笼络军队": "伯爵",
        "祖辈与国王联姻，豪门": "公爵",
        "封疆大吏，国王堂兄弟": "公爵",
        "公爵": "公爵",
        "伯爵": "伯爵",
        "男爵": "男爵",
        "骑士": "骑士",
    }


class ChatMatchingDecider:
    """聊天看相决策器"""

    # 综合评分阈值
    DEFAULT_THRESHOLD = 0.5

    def calculate_score(self, profile: ChatCandidateProfile) -> float:
        """
        综合评分计算

        权重分配（根据用户反馈调整）:
        - 特性: 100% (核心评分依据，优质/劣质特性)
        - 血脉/爵位: 仅记录，不参与评分
        - 年龄: 忽略（废词条）
        """
        score = self._calculate_trait_score(profile)
        profile.total_score = max(0.0, min(score, 1.0))
        return profile.total_score

    def should_accept(
        self, profile: ChatCandidateProfile, threshold: float = None
    ) -> bool:
        """
        判断是否接受该对象

        唯一标准：是否识别到橙色/金色面部特征
        橙色特征 = 直接接受（评分1.0）
        无橙色特征 = 拒绝（评分0.0）
        """
        # 有橙色特征则接受
        if profile.has_orange_feature:
            profile.total_score = 1.0
            return True
        # 无橙色特征则拒绝
        profile.total_score = 0.0
        return False

    def extract_trait_from_message(
        self, text: str, profile: ChatCandidateProfile, race_country_mapping: dict
    ) -> None:
        """
        从媒人话语中提取词条信息

        支持以下区域：
        - 三区：血脉特性（仅记录，不参与评分）
        - 四区：优质特性（笑脸，核心评分）
        - 五区：劣质特性（哭脸，核心评分）
        - 六区：爵位特性（仅记录，不参与评分）
        """
        # 姓名 提取 (他叫/她叫/他的名字是/她的名字是)
        name_match = re.search(
            r"(?:他叫|她叫|他的名字是|她的名字是)([\u4e00-\u9fa5]{1,5})", text
        )
        if name_match:
            profile.name = name_match.group(1)

        # 从描述中提取年龄
        profile.age = self._extract_age_from_text(text)

        # 三区：血脉词条（仅记录）
        self._extract_bloodline_trait(text, profile)

        # 四区：优质特性（笑脸）
        self._extract_positive_traits(text, profile)

        # 五区：劣质特性（哭脸）
        self._extract_negative_traits(text, profile)

        # 六区：爵位词条（仅记录）
        self._extract_title_trait(text, profile, race_country_mapping)

        # 国家/家族（从 race_country_mapping 中查找）
        for race, country in race_country_mapping.items():
            if race in text:
                profile.country = country
                break

    def _extract_positive_traits(
        self, text: str, profile: ChatCandidateProfile
    ) -> None:
        """提取四区优质特性（笑脸）"""
        for keyword, trait_name in TraitConfig.POSITIVE_TRAITS.items():
            if keyword in text and trait_name not in profile.positive_traits:
                profile.positive_traits.append(trait_name)

    def _extract_negative_traits(
        self, text: str, profile: ChatCandidateProfile
    ) -> None:
        """提取五区劣质特性（哭脸）"""
        for keyword, trait_name in TraitConfig.NEGATIVE_TRAITS.items():
            if keyword in text and trait_name not in profile.negative_traits:
                profile.negative_traits.append(trait_name)

    def _extract_bloodline_trait(
        self, text: str, profile: ChatCandidateProfile
    ) -> None:
        """提取三区血脉词条（仅记录，不参与评分）"""
        for pattern, bloodline in TraitConfig.BLOODLINE_PATTERNS.items():
            if pattern in text:
                profile.detected_bloodline_trait = pattern
                if bloodline not in profile.bloodlines:
                    profile.bloodlines[bloodline] = True
                break

    def _extract_title_trait(
        self, text: str, profile: ChatCandidateProfile, race_country_mapping: dict
    ) -> None:
        """提取六区爵位词条（仅记录，不参与评分）"""
        for pattern, title in TraitConfig.TITLE_PATTERNS.items():
            if pattern in text:
                profile.title = title
                return

        # 如果没有匹配到六区词条，使用旧的简单爵位识别
        for t in ["公爵", "伯爵", "男爵", "骑士"]:
            if t in text:
                profile.title = t
                return

    def _extract_age_from_text(self, text: str) -> int:
        """从描述性文本中提取年龄"""
        # 年龄描述 → 年龄范围中值
        age_patterns = {
            "做爷爷": 50,
            "风韵依旧": 40,
            "淘气的小男孩": 17,
            "天真的女孩": 17,
            "很年轻": 22,
            "稚气未脱": 25,
            "已经不是孩子了": 35,
            "成熟的女人": 35,
        }
        for pattern, age in age_patterns.items():
            if pattern in text:
                return age
        return 0

    def _calculate_trait_score(self, profile: ChatCandidateProfile) -> float:
        """
        计算特性综合评分

        特性评分已禁用（权重设为0），
        唯一接受标准是橙色面部特征
        """
        return 0.0
