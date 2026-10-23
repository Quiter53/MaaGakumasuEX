"""Cultivation options; behavior moved from the original action module."""

import json
import time
import random
from typing import Any, Dict, List, Optional

import numpy as np
from utils import logger
from maa.context import Context
from custom.params import action_params
from maa.custom_action import CustomAction
from maa.agent.agent_server import AgentServer

from . import shared


@AgentServer.custom_action("ProduceChooseWorkAuto")
class ProduceChooseWorkAuto(CustomAction):
    """
    处理选择工作类型的窗口

    选择逻辑：
    - 体力 > 10 时：选择会扣除体力对应的工作（health_position 指定的位置）
      - 单个位置 [2] 或 [3]：选择对应的工作
      - 无位置或多位置 [2,3]：随机选择
    - 体力 <= 10 时：选择不扣除体力对应的工作
      - [2] → 选择第三个（位置3不扣体力）
      - [3] → 选择第二个（位置2不扣体力）
      - 其他情况 → 选择第一个
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        first_roi = [100, 760]
        second_roi = [100, 880]
        third_roi = [100, 1000]
        image = context.tasker.controller.post_screencap().wait().get()
        health_data = self._get_health(context, image)
        current = health_data["current"] if health_data else 0

        health_position = self._get_health_position(context, image)

        if current > 10:
            if health_position and len(health_position) == 1:
                box = second_roi if health_position[0] == 2 else third_roi
            else:
                box = random.choice([first_roi, second_roi, third_roi])
        else:
            if health_position == [2]:
                box = third_roi
            elif health_position == [3]:
                box = second_roi
            else:
                box = first_roi
        context.tasker.controller.post_click(box[0], box[1]).wait()
        return True

    @staticmethod
    def _get_health(context: Context, image) -> Optional[dict]:
        """获取当前体力数值"""
        reco_detail = context.run_recognition("ProduceRecognitionHealth", image)
        if not (reco_detail and reco_detail.hit):
            return None

        try:
            health_parts = reco_detail.best_result.text.split("/")
            current_health = int("".join(filter(str.isdigit, health_parts[0])))
            max_health = int("".join(filter(str.isdigit, health_parts[1])))
            ratio = current_health / max_health
            logger.info(f"体力: {current_health}/{max_health} ({ratio:.2%})")
            return {"current": current_health, "max": max_health, "ratio": ratio}
        except (ValueError, IndexError, ZeroDivisionError):
            logger.warning("体力数据解析失败")
            return None

    @staticmethod
    def _get_health_position(context: Context, image) -> Optional[list]:
        """
        获取扣除体力的图标位置

        Returns:
            None: 未检测到扣体力图标
            [2]: 第二个位置扣体力
            [3]: 第三个位置扣体力
            [2,3]: 第二和第三个位置都扣体力
        """
        reco_detail = context.run_recognition(
            "ProduceRecognitionHealthFlag",
            image,
            pipeline_override={"ProduceRecognitionHealthFlag": {"roi": [370, 830, 320, 290]}},
        )
        if reco_detail.hit:
            position = []
            for result in reco_detail.filtered_results:
                health_box_y = result.box[1]
                if health_box_y > 970:
                    position.append(3)
                else:
                    position.append(2)
            return position
        return None


@AgentServer.custom_action("ProduceChooseOptionsAuto")
class ProduceChooseOptionsAuto(CustomAction):
    """
    处理选择选项的窗口

    根据当前得分自动选择属性选项：
    1. 获取第一、第二、第三属性的当前分数
    2. THIRD_ATTR_PROBABILITY 概率选择第三属性，剩余概率选择第二属性
    3. 在可用选项中执行双击选择
    4. 若目标选项不可用，按优先级 fallback：null > 第二属性 > 第三属性 > 随机
    """

    OPTIONS_CONFIG = {
        "Vo": "produce/choose_Vo.png",
        "Da": "produce/choose_Da.png",
        "Vi": "produce/choose_Vi.png",
        "null": "produce/choose_null.png",
    }
    CLICK_DELAY = 0.5
    THIRD_ATTR_PROBABILITY = 0.6  # 选择第三属性的概率，剩余概率选择第二属性
    THIRD_ATTR_FORCE_SECOND_THRESHOLD = 681

    def __init__(self):
        super().__init__()
        self.first = "Vi"
        self.second = "Da"

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        """
        执行自动选择选项

        Returns:
            bool: 选择成功返回 True，否则返回 False
        """
        from custom.hif import _next_targets, outing_header, read_hif_lesson_header

        entry = context.get_node_data("ProduceEntryFlag")
        if isinstance(entry, dict) and "ProduceEntryHIF" in _next_targets(entry.get("next", [])):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if outing_header(context, image):
                return ProduceHIFChooseOutingAuto().run(context, argv)
            if read_hif_lesson_header(context, image):
                return ProduceHIFChooseClassAuto().run(context, argv)
            # HIF has explicit lesson/outing choices. A transient missing
            # title must not switch to the normal scenario's random policy.
            return True
        preference = (
            context.get_node_data("ProduceChooseNIAEventFlag")
            .get("action", {})
            .get("param", {})
            .get("custom_action_param", {"first": "Vo", "second": "Vi"})
        )
        self.first = preference["first"]
        self.second = preference["second"]
        image = context.tasker.controller.post_screencap().wait().get()
        score = self._get_current_score(context, image) or {"Vo": 0, "Da": 0, "Vi": 0, "max": 1}
        params = action_params(argv.custom_action_param)
        fallback = params.get("consecutive_failure_fallback") is True
        if fallback:
            from custom.reco.options_fallback import read_fallback_options

            # Recheck the current frame; never click cached coordinates after a scene change.
            rows = read_fallback_options(context, image)
            if not rows:
                logger.info("保底选项画面已变化，本次不点击，重新识别")
                return True
            options = [{name: box} for name, box, _ in rows]
        else:
            from custom.reco.options_fallback import FAILURE_GATE

            FAILURE_GATE.reset()
            options = self._get_available_options(context, image)

        # 计算选择
        first_score = score.get(self.first, 0)
        second_score = score.get(self.second, 0)
        # 计算第三属性（Vo、Da、Vi 中非 first/second 的那个）
        all_attrs = ["Vo", "Da", "Vi"]
        third = next(attr for attr in all_attrs if attr != self.first and attr != self.second)
        third_score = score.get(third, 0)
        logger.info(f"第一属性 {self.first}={first_score}, 第二属性 {self.second}={second_score}, 第三属性 {third}={third_score}")

        # 第三属性达到控制线后，营业选项固定补第二属性，防止第三属性
        # 继续拉开差距；尚未超过控制线时保留原有随机分配。
        if third_score > self.THIRD_ATTR_FORCE_SECOND_THRESHOLD:
            choice = self.second
            logger.info(f"第三属性 {third}={third_score} > {self.THIRD_ATTR_FORCE_SECOND_THRESHOLD}，营业固定选择第二属性: {choice}")
        elif random.random() < self.THIRD_ATTR_PROBABILITY:
            choice = third
            logger.debug(f"随机数 < {self.THIRD_ATTR_PROBABILITY}，选择第三属性: {choice}")
        else:
            choice = self.second
            logger.debug(f"随机数 >= {self.THIRD_ATTR_PROBABILITY}，选择第二属性: {choice}")

        # 找到目标选项
        target_box = None
        for opt in options:
            if choice in opt:
                target_box = opt[choice]
                break

        # 如果目标选项不可用，尝试 fallback（null 优先）
        if target_box is None:
            logger.debug(f"选项 {choice} 不可用，尝试 fallback")
            # 按 null > 第二属性 > 第三属性 > 随机的优先级选择
            priority = ["null", self.second, third, None]
            for p in priority:
                if p is None:
                    # 最后从所有可用选项中随机选择
                    if options:
                        random_opt = random.choice(options)
                        choice = next(iter(random_opt.keys()))
                        target_box = random_opt[choice]
                        logger.debug(f"Fallback 随机选择: {choice}")
                    break
                for opt in options:
                    if p in opt:
                        target_box = opt[p]
                        choice = p
                        logger.debug(f"Fallback 选择 {p}")
                        break
                if target_box:
                    break

        # 执行双击
        if target_box:
            # box 格式为 [x, y, w, h]，计算中心点
            center_x = target_box[0] + target_box[2] // 2
            center_y = target_box[1] + target_box[3] // 2
            self._double_click(context, center_x, center_y)
            logger.info(f"已选择选项: {choice}, 坐标: ({center_x}, {center_y})")
        else:
            logger.warning("没有可用选项，无法选择")
            return True  # 没有选项可选时默认返回True，避免卡死在这里
        return True

    @staticmethod
    def _get_current_score(context: Context, image) -> Optional[dict]:
        """获取当前得分"""
        score = {
            "Vo": 0,
            "Da": 0,
            "Vi": 0,
            "max": 0,
        }
        roi0_list = [[70 + i * 230, 430, 136, 80] for i in range(3)]
        roi1_list = [[150 + i * 150, 325, 136, 80] for i in range(3)]
        first_reco = context.run_recognition(
            "ProduceRecognitionScore",
            image,
            pipeline_override={"ProduceRecognitionScore": {"roi": roi0_list[0]}},
        )
        use_roi_list = roi1_list if not (first_reco and first_reco.hit) else roi0_list

        for i in range(3):
            reco_detail = context.run_recognition(
                "ProduceRecognitionScore",
                image,
                pipeline_override={"ProduceRecognitionScore": {"roi": use_roi_list[i]}},
            )
            if reco_detail and reco_detail.hit and len(reco_detail.filtered_results) == 2:
                current_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[0].text)))
                max_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[1].text.replace("/", ""))))
                logger.debug(f"第{i + 1}列得分: {current_score} / {max_score}")
                score[["Vo", "Da", "Vi"][i]] = current_score
                score["max"] = max_score if score["max"] < max_score < 9999 else score["max"]
        try:
            logger.info(f"当前得分: Vo={score['Vo']}, Da={score['Da']}, Vi={score['Vi']}, Max={score['max']}")
            return score
        except ValueError:
            logger.warning("积分数据解析失败")
            return None

    def _get_available_options(self, context: Context, image) -> List[Dict[str, Any]]:
        """获取可用选项列表"""
        available_options = []
        available_options_name = ""

        for option_name, option_img in self.OPTIONS_CONFIG.items():
            reco_detail = context.run_recognition(
                "ProduceWorkOptionsTemplate",
                image,
                pipeline_override={"ProduceWorkOptionsTemplate": {"template": option_img}},
            )
            if reco_detail and reco_detail.hit:
                available_options.append({option_name: reco_detail.best_result.box})
                available_options_name += f"{option_name}, "

        logger.info(f"可用选项: {available_options_name.rstrip(', ')}")
        return available_options

    def _double_click(self, context: Context, x: int, y: int):
        """执行双击操作"""
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(x, y).wait()


@AgentServer.custom_action("ProduceChooseMirrorAuto")
class ProduceChooseMirrorAuto(CustomAction):
    """
    自动选择试镜挑战难度

    根据当前投票数在对应试镜的阈值列表中选择满足条件的最高难度：
    - 获取当前投票数和试镜各门槛分数及坐标
    - 选取满足 vote >= threshold 的最高门槛
    - 根据降档配置先跳过指定档数，再检测锁定，锁定则继续降档
    - 降档至 threshold=0 或找到未锁定选项为止
    """

    CLICK_DELAY = 0.5

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        vote = self._get_current_vote(context, image) or 1
        mirror = self._get_current_mirror(context, image)
        lowering_difficulty = self._get_lowering_difficulty(context, argv)

        # 门槛分数降序排列
        thresholds = sorted((int(k) for k in mirror.keys()), reverse=True)

        # 找到满足当前投票的最高门槛索引
        start_idx = 0
        for i, threshold in enumerate(thresholds):
            if vote >= threshold:
                start_idx = i
                break

        # 先降指定档数，再检查锁定，锁定则继续降档直至成功或为0档
        target_threshold = 0
        x = y = 0
        start = min(start_idx + lowering_difficulty, len(thresholds) - 1)
        for i in range(start, len(thresholds)):
            target_threshold = thresholds[i]
            target_box = mirror[str(target_threshold)]
            x = target_box[0] + target_box[2] // 2
            y = target_box[1] + target_box[3] // 2

            if target_threshold == 0:
                break
            if not self._check_lock(context, image, target_box):
                break

            logger.info(f"分数 {target_threshold:,} 已锁定，继续降档")

        # 在点击前判断当前是第几镜，若启用 focus 则点击后进入手动接管
        mirror_idx = self._get_focus_mirror_index(context, image)

        logger.info(f"当前投票: {vote:,}, 目标分数: {target_threshold:,}, 点击坐标: ({x}, {y - 20})")
        self._double_click(context, x, y - 20)

        if not mirror_idx:
            return True

        logger.info(f"检测到 focus 配置，进入手动接管模式（第{mirror_idx}镜）")
        context.run_action(
            "ProduceMirrorFocus",
            pipeline_override={
                "ProduceMirrorFocus": {
                    "focus": {
                        "Node.ActionNode.Succeeded": {
                            "content": f"[color:LimeGreen]已暂停自动培育进入手动接管状态（第{mirror_idx}镜）[/color]\n"
                            '退出手动接管请点击下方"确定"按钮',
                            "display": ["log", "modal", "notification"],
                        }
                    }
                }
            },
        )

        return True

    @staticmethod
    def _get_focus_mirror_index(context: Context, image) -> int:
        """获取当前试镜对应的 focus 镜号。"""
        node_data = context.get_node_data("ProduceMirrorFlag")
        if not node_data:
            return 0
        attach = node_data.get("attach", {})
        logger.debug(f"focus 配置: {attach}")
        # 仅当有 focus_x 为 true 时才识别
        has_focus = any(attach.get(f"focus_{i}", False) for i in range(1, 4))
        if not has_focus:
            return 0

        for i in range(1, 4):
            reco_detail = context.run_recognition(
                f"ProduceMirrorFlag_{i}",
                image,
                pipeline_override={
                    f"ProduceMirrorFlag_{i}": {
                        "recognition": "TemplateMatch",
                        "roi": [12, 630, 696, 530],
                        "template": f"produce/NIA/mirror_{i}.png",
                        "threshold": 0.9,
                    }
                },
            )
            if reco_detail and reco_detail.hit:
                return i if attach.get(f"focus_{i}", False) else 0
        return 0

    @staticmethod
    def _check_lock(context: Context, image, target_box) -> bool:
        """检查目标分数附近是否有锁定图标"""
        roi = [
            target_box[0] + 330,
            target_box[1] - 80,
            100,
            100,
        ]
        reco_detail = context.run_recognition(
            "ProduceRecognitionLock",
            image,
            pipeline_override={
                "ProduceRecognitionLock": {
                    "recognition": "TemplateMatch",
                    "roi": roi,
                    "template": "produce/lock.png",
                    "green_mask": True,
                }
            },
        )
        return reco_detail is not None and reco_detail.hit

    @staticmethod
    def _get_lowering_difficulty(context: Context, argv) -> int:
        """检查是否启动降档"""
        params = json.loads(argv.custom_action_param)
        lowering_level = params.get("level") if isinstance(params, dict) else None
        if not lowering_level:
            return 0
        else:
            logger.info(f"检测到启动降档，降低{lowering_level}档")
            return int(lowering_level)

    @staticmethod
    def _get_current_mirror(context: Context, image):
        """获取当前试镜"""
        mirror = {"0": [360, 1050, 1, 1]}
        reco_detail = context.run_recognition("ProduceRecognitionMirror", image)
        if reco_detail and reco_detail.hit:
            for result in reco_detail.filtered_results:
                box = result.box
                text = "".join(filter(lambda c: c.isdigit(), result.text.replace(",", "")))
                mirror[text] = [box[0], box[1], box[2], box[3]]
        mirror = dict(sorted(mirror.items(), key=lambda x: int(x[0])))
        logger.info(f"当前试镜门槛分数: {list(mirror.keys())}")
        return mirror

    @staticmethod
    def _get_current_vote(context: Context, image) -> Optional[int]:
        """获取当前投票"""
        reco_detail = context.run_recognition("ProduceRecognitionVote", image)
        if reco_detail and reco_detail.hit:
            try:
                vote = int(reco_detail.best_result.text.replace(",", ""))
                return vote
            except ValueError:
                logger.warning("投票数据解析失败")
                return None

    def _double_click(self, context: Context, x: int, y: int):
        """执行双击操作"""
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(x, y).wait()


class ProduceHIFAttributeBase(CustomAction):
    """Shared fixed HIF main-battle attribute selection for Shinozawa Hiro."""

    ATTR_LABELS = {
        "Vo": ("ボーカル", "Vo"),
        "Da": ("ダンス", "Da"),
        "Vi": ("ビジュアル", "Vi"),
    }

    @staticmethod
    def _strategy(context: Context) -> dict:
        return action_params((context.get_node_data("ProduceHIFStrategy") or {}).get("attach"))

    @staticmethod
    def _attribute_order(context: Context) -> dict:
        node = context.get_node_data("ProduceChooseNIAEventFlag") or {}
        preference = action_params(node.get("action", {}).get("param", {}).get("custom_action_param", {}))
        first = preference.get("first", "Vo")
        second = preference.get("second", "Da")
        if first not in ("Vo", "Da", "Vi"):
            first = "Vo"
        if second not in ("Vo", "Da", "Vi") or second == first:
            second = next(attr for attr in ("Vo", "Da", "Vi") if attr != first)
        third = next(attr for attr in ("Vo", "Da", "Vi") if attr not in (first, second))
        return {"first": first, "second": second, "third": third}

    @classmethod
    def _selected_attribute(cls, context: Context, setting: str) -> str:
        ordinal = cls._strategy(context).get(setting, "first")
        return cls._attribute_order(context).get(ordinal, "Vo")

    @staticmethod
    def _ocr(context: Context, image, expected: list[str], roi: list) -> list:
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "recognition": "OCR",
                    "roi": roi,
                    "expected": expected,
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            return []
        return list(detail.filtered_results or detail.all_results or [])

    @staticmethod
    def _click_box(context: Context, box: list, double: bool = True) -> None:
        point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        context.tasker.controller.post_click(*point).wait()
        if double:
            time.sleep(0.3)
            context.tasker.controller.post_click(*point).wait()

    def _run_lesson(self, context: Context, event_text: str, label: str) -> bool:
        from custom.hif import read_hif_events, read_hif_choices, lesson_observation, read_hif_lesson_header, lesson_observations_stable

        attribute = None
        try:
            option = min(3, max(1, int(self._strategy(context).get("lesson_option", 1))))
        except (TypeError, ValueError):
            option = 1
        previous = None
        previous_image = None
        clicked_stage = None
        clicked_image = None
        missing = 0
        clicks = 0
        deadline = time.monotonic() + 14
        if context.tasker.stopping:
            return True
        while time.monotonic() < deadline and not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True  # Let the outer entry handle network/date recovery.
            choices = read_hif_choices(context, image)
            if choices:
                stage, rows = choices
                missing = 0
                # Border animations move detected edges by 1-2px even when
                # the button is ready. Attribute identity must still agree.
                signature = lesson_observation(stage, rows)
                if not lesson_observations_stable(previous, signature, previous_image, image):
                    previous = signature
                    previous_image = image.copy()
                    time.sleep(0.18)
                    continue
                if stage == "attribute":
                    attribute = attribute or self._lesson_attribute(context, image, rows, event_text)
                    if attribute is None:
                        return True
                    row = next((row for row in rows if row[0] == attribute), None)
                    if row is None:
                        logger.warning(f"HIF{label}: 未找到设置的属性 {attribute}，保留画面重识别")
                        return True
                else:
                    row = rows[option - 1]
                if clicked_stage == stage and clicks >= 3:
                    break
                box = list(row[1])
                self._click_box(context, box, double=False)
                clicks = clicks + 1 if clicked_stage == stage else 1
                clicked_stage, clicked_image = stage, image.copy()
                previous = None
                logger.info(
                    f"HIF{label}: 点击{'属性 ' + attribute if stage == 'attribute' else '第 ' + str(option) + ' 选项'} box={box}，等待页面推进"
                )
            elif events := read_hif_events(context, image, event_text):
                missing = 0
                signature = lesson_observation("event", events)
                if not lesson_observations_stable(previous, signature, previous_image, image):
                    previous = signature
                    previous_image = image.copy()
                    time.sleep(0.18)
                    continue
                # Read course/SP state from the stable frame, not a transition frame.
                attribute = attribute or self._lesson_attribute(context, image, events, event_text)
                if attribute is None:
                    return True
                row = next((row for row in events if row[0] == attribute), None)
                if row is None:
                    logger.warning(f"HIF{label}: 当前事件没有设置的属性 {attribute}，交回主流程")
                    return True
                if clicked_stage == "event" and clicks >= 3:
                    break
                self._click_box(context, list(row[1]), double=False)
                clicks = clicks + 1 if clicked_stage == "event" else 1
                clicked_stage, clicked_image = "event", image.copy()
                previous = None
                logger.info(f"HIF{label}: 点击日程入口 {attribute} box={row[1]}，等待属性/选项页面")
            elif read_hif_lesson_header(context, image):
                # The preview retains its header even if a scrim/long label
                # defeats both row readers. Missing rows are NOT completion.
                previous = None
                missing = 0
                time.sleep(0.35)
                continue
            elif clicked_image is not None:
                previous = None  # Occlusion breaks the consecutive-frame check.
                # A click is not a success. Require two changed, row-free frames;
                # animations/occlusions of an unchanged screen do not count.
                before = clicked_image[640:960:4, 20:700:4].astype(np.float32)
                after = image[640:960:4, 20:700:4].astype(np.float32)
                changed = before.shape == after.shape and np.mean(np.abs(before - after)) > 8
                missing = missing + 1 if changed else 0
                if missing >= 2:
                    selected = (
                        "第 " + str(option) + " 选项"
                        if clicked_stage == "option"
                        else attribute + (" 日程入口" if clicked_stage == "event" else " 属性")
                    )
                    logger.info(f"HIF{label}: {selected}点击后已离开课程页，交回主流程识别后续页面")
                    return True
            else:
                logger.info(f"HIF{label}: 当前未识别到三选项或已验证的日程入口，交回主流程")
                return True
            # Allow the click/preview animation, then require a fresh stable
            # pair before retrying. Missing rows are sampled independently.
            time.sleep(0.35 if missing else min(0.85, 0.45 + max(0, clicks - 1) * 0.2))
        if context.tasker.stopping:
            return True
        logger.warning(f"HIF{label}: 14秒内未确认课程选择完成（点击{clicks}次），保留画面；不再将漏识别当作成功或空转等待")
        return False

    def _lesson_attribute(self, context, image, rows, event_text):
        return self._selected_attribute(context, "lesson_attribute")


@AgentServer.custom_action("ProduceHIFChooseClassAuto")
class ProduceHIFChooseClassAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.hif import read_hif_choice_kind

        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if read_hif_choice_kind(context, image) == "course":
            return ProduceHIFChooseCourseAuto().run(context, argv)
        return self._run_lesson(context, "授業", "上课")


@AgentServer.custom_action("ProduceHIFChooseCourseAuto")
class ProduceHIFChooseCourseAuto(ProduceHIFAttributeBase):
    def _lesson_attribute(self, context, image, rows, event_text):
        from custom.hif import hif_course_sp_roi
        from custom.event_templates import HIF_TEMPLATES

        from .events import ProduceChooseNIAEventAuto

        recognizer = ProduceChooseNIAEventAuto()
        recognizer.SP_TEMPLATE = HIF_TEMPLATES["SP"]
        recognizer.SP_TEMPLATE_PARAMS = {"threshold": 0.85}
        order = self._attribute_order(context)
        courses = []
        for attribute, box, *_ in rows:
            if attribute not in order.values():
                continue
            # Only the verified course tile; never search other event icons.
            sp = recognizer._get_sp_course(context, image, hif_course_sp_roi(image, rows, box))
            courses.append({"attribute": attribute, "SP": sp})
        # HIF SP rewards take precedence over every normal course. Attribute
        # scores and teacher suggestions do not override this six-level order.
        available = "/".join(f"{course['attribute']}={'SP' if course['SP'] else '普通'}" for course in courses)
        for need_sp in (True, False):
            for ordinal, attribute in order.items():
                if any(course["attribute"] == attribute and course["SP"] == need_sp for course in courses):
                    priority = {"first": "第一", "second": "第二", "third": "第三"}[ordinal]
                    logger.info(
                        f"HIF课程: 可选={available} 第一={order['first']} 第二={order['second']} "
                        f"第三={order['third']} 选择={attribute} 原因={priority}属性{'SP' if need_sp else '普通'}"
                    )
                    return attribute
        logger.warning("HIF课程: 未识别到可选属性，保留页面重识别")
        return None

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        return self._run_lesson(context, "レッスン", "课程")


@AgentServer.custom_action("ProduceHIFChooseSupplyAuto")
class ProduceHIFChooseSupplyAuto(ProduceHIFAttributeBase):
    """Choose the configured third-day activity on fresh schedule frames."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.hif import _ocr, read_schedule, schedule_choice

        choice = schedule_choice(self._strategy(context))
        previous = None
        for _ in range(4):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            rows = read_schedule(context, image)
            # Only the outing/supply activity page may use the new setting.
            # Missing a requested tile must never silently select supply.
            if choice != "supply" and not {"outing", "supply"}.issubset(rows):
                return True
            box = rows.get(choice)
            if box is None:
                return True
            if previous and all(abs(a - b) <= 4 for a, b in zip(previous, box)):
                if choice == "rest" and _ocr(context, image, [540, 760, 180, 160], [r"^(?:あと)?\s*0\s*回$"]):
                    return True
                if context.tasker.stopping:
                    return True
                self._click_box(context, box, double=False)
                label = {"outing": "外出", "supply": "支给", "rest": "休息"}[choice]
                logger.info(f"HIF第三周：选择{label}")
                return True
            previous = box
            time.sleep(0.2)
        return True


@AgentServer.custom_action("ProduceHIFChooseOutingAuto")
class ProduceHIFChooseOutingAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.hif import outing_choice, lesson_observation, read_outing_choices, lesson_observations_stable

        option = outing_choice(self._strategy(context))
        previous = before = None
        for _ in range(6):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            rows = read_outing_choices(context, image)
            if len(rows) != 3:
                previous = before = None
                time.sleep(0.2)
                continue
            signature = lesson_observation("outing", rows)
            if lesson_observations_stable(previous, signature, before, image):
                if context.tasker.stopping:
                    return True
                self._click_box(context, list(rows[option - 1][1]), double=False)
                logger.info(f"HIF外出：选择第{option}项")
                return True
            previous, before = signature, image.copy()
            time.sleep(0.2)
        # Yield to the outer entry/watchdog, keeping the requested choice.
        # Its title recognizer prevents a generic chooser from taking over.
        return True


@AgentServer.custom_action("ProduceKeepDrinkAuto")
class ProduceKeepDrinkAuto(CustomAction):
    """Keep four bottles by source-aware target priority in the overflow list."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.drink_overflow import handle_overflow

        return handle_overflow(context)
