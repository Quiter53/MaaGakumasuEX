"""Cultivation battle; behavior moved from the original action module."""

import time
from typing import Optional

from utils import logger
from maa.context import Context
from custom.params import action_params
from custom.card_rules import RuleStore, name_key, rule_for, combo_target
from custom.card_rules import allowed as rule_allowed
from custom.card_rules import sort_key as rule_sort_key
from maa.custom_action import CustomAction
from custom.card_visual import classify_rarity, rarity_priority, match_card_catalog, intersection_over_union
from custom.battle_combo import NATIONAL, NationalCombo
from custom.battle_drinks import BattleDrinkSession
from custom.frame_geometry import same_card_slot
from maa.agent.agent_server import AgentServer

from . import shared


@AgentServer.custom_action("ProduceCardsAuto")
class ProduceCardsAuto(CustomAction):
    """
    初、NIA、HIF 共用出牌策略：再动优先，同类按 SSR > SR > R > N。

    稀有度或再动无法确定时会写出完整日志，并回退到系统推荐牌/检测
    分数最高的牌。使用回合与同档排序读取用户保存的当前规则预设。
    无法核实卡名时降级原策略，保持自动运行。
    """

    # 阈值常量
    CLICK_DELAY = 0.3
    TIME_OUT = 15.0
    RECOGNITION_STALL_TIMEOUT = 45.0
    POST_PLAY_RECOGNITION_DELAY = 1.5

    @classmethod
    def _configured_post_play_delay(cls, raw):
        value = action_params(raw).get("post_play_delay", cls.POST_PLAY_RECOGNITION_DELAY)
        if isinstance(value, bool):
            return cls.POST_PLAY_RECOGNITION_DELAY
        try:
            delay = float(value)
        except (TypeError, ValueError):
            return cls.POST_PLAY_RECOGNITION_DELAY
        return delay if 0 <= delay <= 5 else cls.POST_PLAY_RECOGNITION_DELAY

    # 保留定制版对底部重叠手牌的兼容范围，并限制异常框的下界。
    CARD_Y_MIN = 780
    CARD_Y_MAX = 1000

    def _rule_preset(self):
        if not hasattr(self, "_rules_store"):
            self._rules_store = RuleStore()
        return self._rules_store.preset()

    def _held_until_late(self, name, remaining):
        return not rule_allowed(rule_for(self._rule_preset(), name), remaining)

    def _needs_rule_identity(self, remaining):
        return any(r["enabled"] and not rule_allowed(r, remaining) for r in self._rule_preset()["rules"])

    def _play_candidate(self, context, image, candidates):
        selected = self._choose_card(context, image, candidates)
        if selected is not None:
            self._held_hand_check = None
            return self._play_a_card(context, selected["box"])
        # Only skip after three fresh, agreeing hands consisting entirely of
        # held cards (including repeatedly observed finisher hints).
        remaining = self._drinks.turns.read(context, image)
        key = (remaining, tuple(sorted(tuple(c["box"]) for c in candidates)))
        previous, count = getattr(self, "_held_hand_check", None) or (None, 0)
        agrees = (
            previous is not None
            and previous[0] == remaining
            and len(previous[1]) == len(key[1])
            and all(self._same_selected_box(a, b) for a, b in zip(previous[1], key[1]))
        )
        count = count + 1 if agrees else 1
        self._held_hand_check = (key, count)
        self.start_time = time.time()
        if count >= 3:
            logger.info("连续三帧仅剩需暂缓的牌，自动跳过回合后继续")
            self._skip_held_round(context)
        return True

    def _skip_held_round(self, context):
        if context.tasker.stopping or self._disconnect_detected:
            return
        self._drinks.opening = False
        self._drinks.pending_card = None
        context.run_task("ProduceRecognitionSkipRound")
        self._wait_until_playable(context)
        self._held_hand_check = None
        self._held_selection = None
        self._held_reselections = 0
        self.start_time = time.time()

    def _defer_held_card(self, context, name, box, remaining, *, unconfirmed=False):
        """Leave the card selected so a fresh hand scan can select another card.

        This observation is used only while that same raised card is still
        visible. Bound repeated re-selections rather than looping or failing.
        """
        if remaining is None:
            return self._recover_card_selection(context)
        self._held_selection = (name, list(box), True) if unconfirmed else (name, list(box))
        self._held_reselections = getattr(self, "_held_reselections", 0) + 1
        self._drinks.pending_card = None
        logger.debug(f"规则时机暂缓：{name} 总剩余={remaining} 待核名={unconfirmed}")
        if self._held_reselections >= 6:
            logger.debug("受限牌重复选中达到上限，跳过当前回合")
            self._skip_held_round(context)
        self.start_time = time.time()
        return True

    def __init__(self):
        super().__init__()
        self.start_time = time.time()
        self._disconnect_detected = False
        self._post_play_delay = self.POST_PLAY_RECOGNITION_DELAY
        self._combo = NationalCombo()
        self._combo_hand = []
        self._combo_boxes = []

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        from custom.hand_identity import hand_boxes

        from .shop import ProduceShoppingAuto

        self._disconnect_detected = False
        # Reset for each task invocation; switching instances must not inherit
        # another task's delay. Old configurations retain the 1.5s default.
        self._post_play_delay = self._configured_post_play_delay(getattr(argv, "custom_action_param", None))
        logger.debug(f"出牌后额外等待：{self._post_play_delay:g} 秒")
        # 使用饮料
        self._drink_error = False
        self._combo = NationalCombo()
        self._combo_hand = []
        self._combo_boxes = []
        if not self._wait_until_playable(context):
            return not self._drink_error
        if self._disconnect_detected:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = shared._detect_disconnect_screen(context, image)
        if disconnect_node:
            logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
            return True
        try:
            self._drinks = BattleDrinkSession()
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"饮料策略读取失败，本场保留饮料并继续出牌：{exc}")
            self._drinks = BattleDrinkSession(policy={})
        self._drink_error = False
        self._held_hand_check = None
        self._drinks.combo_enabled = bool(combo_target(self._rule_preset()))
        self._held_selection = None
        self._held_reselections = 0

        # 开始出牌
        self._selection_failures = 0
        self.start_time = time.time()
        unusable_confirmations = 0
        no_cards_confirmations = 0
        shop_visit_closed = False
        while True:
            # 处理手动终止任务
            if context.tasker.stopping:
                logger.info("任务中断")
                return True

            # 截图
            image = context.tasker.controller.post_screencap().wait().get()
            disconnect_node = shared._detect_disconnect_screen(context, image)
            if disconnect_node:
                logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
                return True

            # 通过检测体力槽判断是否处于出牌场景
            if not self._is_playing_card(context, image):
                image = self._recheck_battle_frame(context)
                if context.tasker.stopping or self._disconnect_detected:
                    return True
                if image is None:
                    logger.info("连续三帧未确认战斗体力条，交回场景识别")
                    logger.success("事件: 退出出牌")
                    break

            if not shop_visit_closed:
                # Interval shops can finish their exit animation after their
                # callback returned. A confirmed battle closes that visit.
                ProduceShoppingAuto._reset_visit(context)
                shop_visit_closed = True

            if self._drink_step(context, image):
                if self._drink_error:
                    return False
                self.start_time = time.time()
                continue

            # 识别手牌
            raw_candidates = []
            self._identity_boxes = []
            reco_detail = context.run_recognition("ProduceRecognitionCards", image)
            if reco_detail and reco_detail.hit:
                # Filter all classes before counting, including unusable detections.
                self._identity_boxes = hand_boxes(reco_detail.all_results or [])
                results = [result for result in (reco_detail.all_results or []) if self.CARD_Y_MIN <= result.box[1] <= self.CARD_Y_MAX]
                raw_candidates = self._get_playable_candidates(results, image.shape)
                candidates = self._get_full_hand_candidates(raw_candidates)
                if candidates:
                    unusable_confirmations = 0
                    no_cards_confirmations = 0
                    if not self._play_candidate(context, image, candidates):
                        return False
                elif any(result.label == "useless" for result in results):
                    # A card sliding at either screen edge is sometimes emitted
                    # as a narrow fragment.  The old geometry filter discarded
                    # it and trusted a simultaneous `useless` detection, which
                    # could skip a round despite a playable card being present.
                    relaxed = self._get_relaxed_hand_candidates(raw_candidates)
                    if relaxed:
                        unusable_confirmations = 0
                        no_cards_confirmations = 0
                        logger.warning("无可用牌判定被残缺可用卡框否决；优先尝试检测分数最高的残缺卡")
                        if not self._play_candidate(context, image, relaxed):
                            return False
                        continue

                    unusable_confirmations += 1
                    self._held_hand_check = None
                    no_cards_confirmations = 0
                    if unusable_confirmations < 3:
                        logger.warning(f"检测器报告无可用牌，正在跨帧复核 ({unusable_confirmations}/3)")
                        time.sleep(0.35)
                        continue

                    diagnostic = [
                        {
                            "box": [int(value) for value in result.box],
                            "label": result.label,
                            "detector_score": float(getattr(result, "score", 0.0)),
                        }
                        for result in results
                    ]
                    shared._save_card_diagnostic(image, "no-playable", diagnostic)
                    logger.warning("连续三帧确认无可用牌，执行跳过回合")
                    self._drinks.opening = False
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    unusable_confirmations = 0
                else:
                    unusable_confirmations = 0
                    no_cards_confirmations = 0
                    self._held_hand_check = None

            else:
                self._held_hand_check = None
                reco_detail = context.run_recognition("ProduceRecognitionNoCards", image)
                if reco_detail and reco_detail.hit:
                    no_cards_confirmations += 1
                    unusable_confirmations = 0
                    if no_cards_confirmations < 3:
                        logger.warning(f"未检测到手牌，正在跨帧复核 ({no_cards_confirmations}/3)")
                        time.sleep(0.35)
                        continue
                    shared._save_card_diagnostic(image, "no-cards", [])
                    logger.info("连续三帧确认无手牌")
                    self._drinks.opening = False
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    no_cards_confirmations = 0
                else:
                    # An animation frame can make both recognizers miss.  It is
                    # not evidence for skipping and must reset the confirmation.
                    unusable_confirmations = 0
                    no_cards_confirmations = 0

            elapsed = time.time() - self.start_time
            if elapsed >= self.TIME_OUT:
                # Only use current-frame playable evidence. Never click a stale
                # box or skip a turn merely because recognition timed out.
                relaxed = self._get_relaxed_hand_candidates(raw_candidates)
                if relaxed:
                    logger.warning("出牌等待超时，使用当前帧已确认的残缺可用牌")
                    if not self._play_candidate(context, image, relaxed):
                        return False
                    unusable_confirmations = no_cards_confirmations = 0
                elif elapsed >= self.RECOGNITION_STALL_TIMEOUT:
                    shared._save_card_diagnostic(image, "battle-stalled", [])
                    logger.warning("出牌识别暂未恢复，交回培育入口重新识别；不跳回合、不结束任务")
                    time.sleep(0.5)
                    return True

            time.sleep(self.CLICK_DELAY)

        return True

    @staticmethod
    def _get_playable_candidates(results: list, image_shape) -> list:
        """Keep playable detections and merge duplicate boxes from ``all_results``."""
        screen_height, screen_width = image_shape[:2]
        raw = []
        for result in results:
            if result.label not in ("cards", "suggestions"):
                continue
            box = [int(value) for value in result.box]
            if box[2] < 20 or box[3] < 20:
                continue
            if box[0] + box[2] <= 0 or box[0] >= screen_width:
                continue
            if box[1] + box[3] <= 0 or box[1] >= screen_height:
                continue
            raw.append(
                {
                    "box": box,
                    "label": result.label,
                    "detector_score": float(getattr(result, "score", 0.0)),
                }
            )

        raw.sort(key=lambda item: item["detector_score"], reverse=True)
        unique = []
        for candidate in raw:
            duplicate = next(
                (saved for saved in unique if intersection_over_union(candidate["box"], saved["box"]) >= 0.45),
                None,
            )
            if duplicate is None:
                unique.append(candidate)
            elif candidate["label"] == "suggestions":
                duplicate["label"] = "suggestions"
        return sorted(unique, key=lambda item: item["box"][0])

    @staticmethod
    def _get_relaxed_hand_candidates(candidates: list) -> list:
        return [
            candidate
            for candidate in candidates
            if ProduceCardsAuto.CARD_Y_MIN <= candidate["box"][1] <= ProduceCardsAuto.CARD_Y_MAX
            and 20 <= candidate["box"][2] <= 220
            and 180 <= candidate["box"][3] <= 300
            and candidate["detector_score"] >= 0.65
        ]

    @staticmethod
    def _get_full_hand_candidates(candidates: list) -> list:
        """Keep complete portrait cards in the bottom hand area."""
        return [
            candidate
            for candidate in candidates
            if ProduceCardsAuto.CARD_Y_MIN <= candidate["box"][1] <= ProduceCardsAuto.CARD_Y_MAX
            and 120 <= candidate["box"][2] <= 220
            and 180 <= candidate["box"][3] <= 300
        ]

    @staticmethod
    def _extra_action_status(context: Context, image, box: list) -> tuple:
        """Recognize the blue +1 glyph in the fixed left-side effect column."""
        x, y, width, height = box
        if width < 40 or height < 40:
            return "unknown", 0.0, [x, y, width, height]
        # Effect icons are bottom-aligned in portrait cards and vertically
        # stacked in square cards.  Only the left icon column is relevant; the
        # former broad ROI also included card art and caused intermittent hits.
        roi = [
            max(0, x),
            max(0, y + int(height * 0.18)),
            max(1, int(width * 0.32)),
            max(1, int(height * 0.70)),
        ]
        detail = context.run_recognition(
            "ProduceRecognitionExtraAction",
            image,
            pipeline_override={
                "ProduceRecognitionExtraAction": {
                    "roi": roi,
                    "threshold": 0.35,
                }
            },
        )
        if not (detail and detail.hit and detail.best_result):
            return "no", 0.0, roi
        score = float(getattr(detail.best_result, "score", 0.0))
        # Captured true +1 samples cover 0.546-0.716 with the old broad ROI;
        # ordinary cards produced no match.  The small uncertain band is logged
        # and saved without being promoted to an extra-action card.
        if score >= 0.52:
            return "yes", score, roi
        if score <= 0.46:
            return "no", score, roi
        return "unknown", score, roi

    @staticmethod
    def _apply_card_name_fallback(context: Context, image, evaluated: list) -> None:
        """Use one batched OCR pass to resolve visually uncertain cards.

        Card names remain readable when a disabled overlay washes out the
        rarity frame.  OCR is intentionally skipped when every visual result
        is already strong, so the normal fast path is unchanged.
        """
        uncertain = [
            item
            for item in evaluated
            if item["rarity"] == "unknown"
            or item["rarity_confidence"] < 0.15
            or (item["rarity"] == "N" and item["rarity_confidence"] < 0.58)
            or item["extra_action"] == "unknown"
        ]
        if not uncertain:
            return

        image_height, image_width = image.shape[:2]
        left = max(0, min(item["box"][0] for item in evaluated))
        right = min(
            image_width,
            max(item["box"][0] + item["box"][2] for item in evaluated),
        )
        top = max(
            0,
            min(item["box"][1] + int(item["box"][3] * 0.80) for item in evaluated),
        )
        bottom = min(
            image_height,
            max(item["box"][1] + item["box"][3] for item in evaluated),
        )
        roi = [left, top, max(1, right - left), max(1, bottom - top)]
        detail = context.run_recognition(
            "ProduceRecognitionCardNameFallback",
            image,
            pipeline_override={
                "ProduceRecognitionCardNameFallback": {
                    "recognition": "OCR",
                    "roi": roi,
                    "expected": [],
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            logger.debug(f"卡名兜底OCR未命中 roi={roi}")
            return

        results = list(detail.filtered_results or detail.all_results or [])
        for item in uncertain:
            x, _, width, _ = item["box"]
            pieces = []
            for result in results:
                result_box = list(getattr(result, "box", []) or [])
                if len(result_box) != 4:
                    continue
                rx, _, rw, _ = result_box
                # Maa returns full-image coordinates.  Older framework builds
                # may return ROI-relative boxes, which are normalized here.
                if rx + rw <= roi[2] and rx < roi[0]:
                    rx += roi[0]
                overlap = max(0, min(x + width, rx + rw) - max(x, rx))
                if overlap >= min(width, rw) * 0.25:
                    pieces.append((rx, str(getattr(result, "text", ""))))
            raw_name = "".join(text for _, text in sorted(pieces))
            matched = match_card_catalog(raw_name)
            item["ocr_name"] = raw_name
            if matched is None:
                logger.debug(f"卡名兜底未匹配 box={item['box']} raw={raw_name!r} roi={roi}")
                continue

            previous = item["rarity"]
            # A catalog title meeting the score gate may correct a weak
            # visual result; lower scores may fill unknown only.
            if previous == "unknown" or (
                matched.confidence >= 0.999 and (item["rarity_confidence"] < 0.35 or (previous == "N" and item["rarity_confidence"] < 0.58))
            ):
                item["rarity"] = matched.rarity
                item["rarity_confidence"] = matched.confidence
                item["rarity_source"] = "OCR-card-catalog"
            if item["extra_action"] == "unknown" and matched.extra_action:
                item["extra_action"] = "yes"
                item["extra_score"] = matched.confidence
            item["catalog_name"] = matched.matched_name
            item["catalog_confidence"] = matched.confidence
            item["priority"] = rarity_priority(item["rarity"], item["extra_action"])
            logger.info(
                f"卡名兜底 box={item['box']} raw={raw_name!r} "
                f"matched={matched.matched_name!r} score={matched.confidence:.3f} "
                f"rarity={previous}->{item['rarity']} "
                f"extra_action={item['extra_action']} priority={item['priority']}"
            )

    def _choose_card(self, context: Context, image, candidates: list) -> Optional[dict]:
        preset = self._rule_preset()
        session = getattr(self, "_drinks", None)
        remaining = None
        boxes = getattr(self, "_identity_boxes", [])
        self._selected_identity = None
        identity_results = {}
        identity_names = {}
        held = getattr(self, "_held_selection", None)
        raised_held = None
        if held:
            from custom.battle_drinks import CardNameReader

            raised_held = CardNameReader().locate_raised(context, image, held[1])
            if raised_held is None or not self._same_selected_box(raised_held, held[1]):
                self._held_selection = None
                raised_held = None

        def identify(candidate):
            key = tuple(candidate["box"])
            if key in identity_names:
                return identity_names[key]
            if raised_held is not None and self._same_selected_box(candidate["box"], raised_held):
                # An OCR hint can exclude this still-selected slot before its
                # window, but must not become a confirmed name when it opens.
                if len(held) > 2 and held[2] and not self._held_until_late(held[0], remaining):
                    identity_names[key] = None
                    return None
                identity_names[key] = held[0]
                return held[0]
            if not session or not hasattr(session, "art"):
                return None
            result = session.art.read(image, candidate["box"], boxes)
            identity_results[tuple(candidate["box"])] = result
            if result and result.name:
                identity_names[key] = result.name
                return result.name
            # Unobscured names remain a fallback, never OCR a neighbour's
            # overlapping text after selection with stale hand coordinates.
            if result and result.mode == "full":
                identity_names[key] = session.names.read(context, image, candidate["box"])
                return identity_names[key]
            identity_names[key] = None
            return None

        if session and hasattr(session, "turns"):
            remaining = session.turns.read(context, image)
            allowed = []
            for candidate in candidates:
                name = identify(candidate)
                if self._held_until_late(name, remaining):
                    logger.debug(f"规则时机过滤：{name} 总剩余={remaining}")
                else:
                    allowed.append(candidate)
            candidates = allowed
            if self._held_until_late(session.pending_card, remaining):
                session.pending_card = None
            if not candidates:
                return None
        if session and session.pending_card:
            pinned = [candidate for candidate in candidates if identify(candidate) == session.pending_card]
            if pinned:
                candidates = pinned
            else:
                # A unique tentative art name may locate a card for a fresh
                # raised-title check, but never authorizes drinking/playing.
                hinted = [
                    candidate
                    for candidate in candidates
                    if (result := identity_results.get(tuple(candidate["box"])))
                    and not result.name
                    and result.candidate == session.pending_card
                ]
                if len(hinted) == 1:
                    candidates = hinted
                    logger.info(f"喝药后目标牌卡图待核实，点选后读取完整标题：{session.pending_card}")
                else:
                    logger.warning(f"喝药后未重新定位目标牌「{session.pending_card}」，解除旧目标，按当前可用牌继续出牌")
                    session.pending_card = None
                    self._skip_named_drink_once = True
        evaluated = []
        for index, candidate in enumerate(candidates, start=1):
            rarity = classify_rarity(image, candidate["box"])
            extra_action, extra_score, extra_roi = self._extra_action_status(context, image, candidate["box"])
            priority = rarity_priority(rarity.rarity, extra_action)
            item = {
                **candidate,
                "rarity": rarity.rarity,
                "rarity_confidence": rarity.confidence,
                "rarity_distance": rarity.best_distance,
                "rarity_margin": rarity.margin,
                "rarity_distances": rarity.distances,
                "frame_bgr": [round(value * 255) for value in rarity.signature],
                "rarity_source": rarity.channel_order,
                "extra_action": extra_action,
                "extra_score": extra_score,
                "extra_roi": extra_roi,
                "priority": priority,
            }
            evaluated.append(item)
            distance_text = ", ".join(f"{key}={value:.3f}" for key, value in rarity.distances.items()) or "无"
            shared._diagnostic_info(
                f"卡牌识别[{index}] box={candidate['box']} "
                f"detector={candidate['label']}/{candidate['detector_score']:.3f} "
                f"rarity={rarity.rarity} confidence={rarity.confidence:.3f} "
                f"distance={rarity.best_distance:.3f} margin={rarity.margin:.3f} "
                f"source={rarity.channel_order} frame_bgr={item['frame_bgr']} "
                f"distances={{{distance_text}}} "
                f"extra_action={extra_action} "
                f"extra_score={extra_score:.3f} extra_roi={extra_roi} priority={priority}"
            )

        self._apply_card_name_fallback(context, image, evaluated)

        # Reuse identities already obtained for the finisher guard. No added
        # OCR, click-to-identify, or waiting in the battle hot path.
        for item in evaluated:
            box_key = tuple(item["box"])
            name = identity_names.get(box_key)
            if not name and item.get("catalog_confidence", 0) >= 0.999:
                name = item.get("catalog_name")
                identity_names[box_key] = name
            item["rule_name"] = name

        # A title recovered by catalog fallback must also obey timing rules.
        evaluated = [item for item in evaluated if rule_allowed(rule_for(preset, item.get("rule_name")), remaining)]
        if not evaluated:
            return None
        fallback = all(item["rarity"] == "unknown" for item in evaluated)
        needs_identity = any(r["enabled"] and not rule_allowed(r, remaining) for r in preset["rules"])

        def selection_key(item):
            key = rule_sort_key(preset, item.get("rule_name"), item["rarity"], item["extra_action"], remaining, item["box"][0])
            # Preserve the existing recognition safety tie-break: when timing
            # is restricted, prefer a verified safe name over an unidentified
            # card at the exact same tier/score. Fully identified ties use slots.
            return (*key[:-1], bool(item.get("rule_name")) if needs_identity else False, key[-1])

        selected = max(evaluated, key=selection_key)
        target = combo_target(preset)
        if target and (not selected.get("rule_name") or name_key(selected.get("rule_name")) in (name_key(NATIONAL), name_key(target))):
            # Inspect all detected physical slots, including unusable cards.
            # Unknown identity prevents fetching; it is not evidence of absence.
            self._combo_hand = [identify({"box": b}) for b in boxes]
            self._combo_boxes = boxes
        else:
            self._combo_hand = []
        if selected["rarity"] == "unknown" or selected["rarity_confidence"] < 0.15 or 0.46 < selected["extra_score"] < 0.52:
            shared._save_card_diagnostic(image, "battle", evaluated)
        logger.success(
            f"出牌选择: rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']} fallback={fallback} "
            f"name={selected.get('rule_name')} preset={preset['name']} remaining={remaining} "
            f"rule_key={rule_sort_key(preset, selected.get('rule_name'), selected['rarity'], selected['extra_action'], remaining)}"
        )
        if session and hasattr(session, "art"):
            self._selected_identity = (tuple(selected["box"]), identify(selected))
        return selected

    def _drink_step(self, context, image, card_name=None):
        from custom.battle_move import MoveDisconnected

        try:
            remaining = self._drinks.turns.read(context, image)
            if remaining is not None and remaining != self._drinks.last_log:
                extra = getattr(self._drinks.turns, "extra_turns", 0)
                suffix = f"（主回合 {self._drinks.turns.base_turns} + 追加 {extra}）" if isinstance(extra, int) and extra else ""
                logger.info(f"战斗剩余回合：{remaining}{suffix}")
                self._drinks.last_log = remaining
            fetch = self._combo.fetch_target(self._rule_preset(), card_name, self._combo_hand, remaining)
            return self._drinks.prepare(
                context,
                image,
                self._wait_until_playable,
                remaining=remaining,
                card_name=card_name,
                combo_fetch=fetch,
                move_picker=lambda target: self._fetch_combo_card(context, target, remaining),
            )
        except MoveDisconnected as exc:
            self._disconnect_detected = True
            logger.warning(str(exc))
            return True
        except InterruptedError:
            if context.tasker.stopping:
                return True
            raise
        except (RuntimeError, ValueError, OSError) as exc:
            if self._disconnect_detected:
                return True
            logger.error(f"饮料时机处理未完成：{exc}")
            self._drink_error = True
            return True

    def _fetch_combo_card(self, context, target, remaining):
        from custom.battle_move import MovePicker

        self._combo.fetch_attempted = True
        if not rule_allowed(rule_for(self._rule_preset(), target), remaining):
            logger.warning(f"黑醋配合规则冲突：{target} 当前不满足出牌回合，请调整配合牌规则；取牌后仍按原规则出牌")
        moved = MovePicker(context, self._rule_preset(), remaining, target).run()
        # No pinned card or forced combo: re-run the user's original ranking.
        self._combo_hand = []
        return moved

    @staticmethod
    def _same_selected_box(first, second):
        # This only associates hand slots; selection identity is checked
        # separately against the current tooltip before any play click.
        return same_card_slot(first, second)

    def _observe_selected_card(self, context, original_box):
        from custom.battle_drinks import CardDetection, CardNameReader

        reader = CardNameReader()
        previous = None
        previous_frame = None
        for _ in range(9):
            if context.tasker.stopping:
                return None
            frame = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return None
            detection = CardDetection(context, frame)
            box = reader.locate_raised(context, frame, original_box, detection)
            if previous_frame is not None and reader.same_selection(previous_frame, previous, frame, box):
                return frame, box, detection
            previous, previous_frame = box, frame
            time.sleep(0.18)
        return None

    def _confirm_card_submission(self, context, selected_frame, selected_box):
        """Retry only while the same selected card and tooltip remain visible.

        Disappearance means the click advanced (possibly into an animation),
        not that a new hand may be clicked. The caller waits for that hand.
        """
        from custom.battle_drinks import CardNameReader

        reader = CardNameReader()
        previous = None
        previous_frame = None
        for attempt in range(10):
            if context.tasker.stopping:
                return False
            time.sleep(0.15)
            frame = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return False
            box = reader.locate_raised(context, frame, selected_box)
            if box is None:
                return True
            same_title = reader.same_title_panel(selected_frame, frame)
            if (
                attempt == 4
                and same_title
                and previous_frame is not None
                and reader.same_selection(previous_frame, previous, frame, box)
                and reader.same_selection(selected_frame, selected_box, frame, box)
            ):
                logger.warning("确认出牌点击未生效，同一张选中牌仍在；按新位置补点一次")
                context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
            previous, previous_frame = box, frame
        logger.warning("选中牌尚未出手，稍后重新观察；不结束任务")
        return False

    def _recover_card_selection(self, context):
        """Re-observe instead of waiting for our own open tooltip to disappear."""
        if context.tasker.stopping or self._disconnect_detected:
            return True
        self._selection_failures = getattr(self, "_selection_failures", 0) + 1
        if self._selection_failures >= 3:
            frame = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return True
            skip = context.run_recognition("ProduceRecognitionSkipRound", frame)
            if skip and skip.hit:
                logger.warning("连续三次未能确认选中状态，自动跳过本回合后继续，不结束任务")
                self._skip_held_round(context)
                self._selection_failures = 0
        time.sleep(0.35)
        self.start_time = time.time()
        return True

    def _resolve_selected_rule_turns(self, context, frame, box, name, remaining):
        """Recheck missing turns on the same selected card, without reselecting.

        A failed +N OCR is not evidence that a configured window is closed.
        Fresh frames must still show this selection; never reuse an old total
        after a drink/animation or when an extra turn may have been added.
        """
        rule = rule_for(self._rule_preset(), name)
        if remaining is not None or not rule or not rule["limited"]:
            return frame, box, remaining
        from custom.battle_drinks import CardNameReader

        for _ in range(3):
            if context.tasker.stopping or self._disconnect_detected:
                return None
            time.sleep(0.15)
            current = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, current):
                self._disconnect_detected = True
                return None
            current_box = CardNameReader().locate_raised(context, current, box)
            if not CardNameReader.same_selection(frame, box, current, current_box):
                return None
            remaining = self._drinks.turns.read(context, current)
            if remaining is not None:
                return current, current_box, remaining
        logger.debug("选中牌回合数暂未确认，交回观察；不计入规则暂缓次数")
        return None

    def _play_a_card(self, context: Context, box: list) -> bool:
        """Select -> observe selection -> optional drinks -> confirm -> observe play."""
        from custom.battle_drinks import CardDetection, CardNameReader

        name = None
        if context.tasker.stopping:
            return True
        if getattr(self, "_drink_error", False):
            return False
        identity = getattr(self, "_selected_identity", None)
        self._selected_identity = None  # valid for exactly this selection
        original_box = list(box)
        before = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, before):
            self._disconnect_detected = True
            return True
        if combo_target(self._rule_preset()) and self._combo_hand:
            from custom.hand_identity import hand_boxes

            detail = context.run_recognition("ProduceRecognitionCards", before)
            current_boxes = hand_boxes(detail.all_results or []) if detail and detail.hit else []
            # Recheck the hand immediately before selection. Any transition,
            # raised layout or unknown slot means no speculative vinegar use.
            current_names = []
            for current_box in current_boxes:
                art = self._drinks.art.read(before, current_box, current_boxes)
                current_names.append(
                    art.name
                    if art and art.name
                    else self._drinks.names.read(context, before, current_box)
                    if art and art.mode == "full"
                    else None
                )
            self._combo_hand = current_names if current_boxes and len(current_boxes) == len(self._combo_boxes) else []
            self._combo_boxes = current_boxes
            if identity:
                for i, current_box in enumerate(current_boxes):
                    if self._same_selected_box(current_box, original_box) and current_names[i] != identity[1]:
                        identity = None
                        break
        # The previous confirm click can be swallowed. Do not "select" that
        # raised card again and then try to OCR it after it has flown away.
        already_selected = CardNameReader().locate_raised(context, before, box)
        if already_selected is None:
            context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
            time.sleep(0.40)
        observed = self._observe_selected_card(context, original_box)
        if observed is None:
            logger.warning("未确认卡牌选中，可能仍在动画或点击未接收；重新观察当前手牌，不结束任务")
            return self._recover_card_selection(context)
        selected_frame, box, selected_detection = observed
        # At least .58s from the selection click plus recognition; the old
        # fixed .30s could hit while the card was still rising.
        if getattr(self, "_drinks", None) is not None:
            name = identity[1] if identity and identity[0] == tuple(original_box) else None
            remaining = self._drinks.turns.read(context, selected_frame) if hasattr(self._drinks, "turns") else None
            guard_active = hasattr(self._drinks, "turns")
            needs_guard_name = guard_active and self._needs_rule_identity(remaining)
            skip_named = getattr(self, "_skip_named_drink_once", False)
            self._skip_named_drink_once = False
            if name is None and (needs_guard_name or (not skip_named and self._drinks.needs_card_name(context, selected_frame))):
                previous = None
                previous_frame = None
                previous_hint = None
                previous_hint_frame = None
                suspected = None
                for attempt in range(5):
                    if context.tasker.stopping:
                        return True
                    observed = self._drinks.names.read_raised(context, selected_frame, original_box, detection=selected_detection)
                    hint = getattr(self._drinks.names, "last_raised_candidate", None)
                    # Two fresh, associated selections are required even to
                    # defer a suspected finisher; one weak OCR is insufficient.
                    suspected = None
                    if (
                        isinstance(hint, dict)
                        and isinstance(previous_hint, dict)
                        and hint["name"] == previous_hint["name"]
                        and self._held_until_late(hint["name"], remaining)
                        and CardNameReader.same_selection(previous_hint_frame, previous_hint["box"], selected_frame, hint["box"])
                    ):
                        suspected = hint
                    previous_hint = hint
                    previous_hint_frame = selected_frame
                    if (
                        observed
                        and previous
                        and observed["name"] == previous["name"]
                        and CardNameReader.same_selection(previous_frame, previous["box"], selected_frame, observed["box"])
                    ):
                        name = observed["name"]
                        box = observed["box"]
                        break
                    previous = observed
                    previous_frame = selected_frame
                    if attempt < 4:
                        time.sleep(0.20)
                        selected_frame = context.tasker.controller.post_screencap().wait().get()
                        selected_detection = CardDetection(context, selected_frame)
                        if shared._detect_disconnect_screen(context, selected_frame):
                            self._disconnect_detected = True
                            return True
                if name is None:
                    if needs_guard_name and suspected:
                        logger.debug(f"受限牌名称待核实：{suspected['name']} OCR={suspected['score']:.4f}")
                        return self._defer_held_card(context, suspected["name"], suspected["box"], remaining, unconfirmed=True)
                    if needs_guard_name:
                        logger.warning("卡名复核仍不确定，降级原出牌策略继续运行，本次不触发指定卡牌前饮料")
                    shared._save_card_diagnostic(
                        selected_frame, "battle-name-unconfirmed", [dict(box=box, reason="before-card drink requires identity")]
                    )
                    logger.warning("待出牌名称暂未确认，本次不触发指定卡牌前饮料；继续当前可用牌，不结束任务")
                    skip_named = True
            if name:
                logger.info(f"待出牌：{name}")
                if self._combo_hand and self._combo_boxes:
                    # A newly confirmed raised title can fill only its own
                    # formerly unknown slot, never a neighbour's identity.
                    for i, slot in enumerate(self._combo_boxes):
                        if self._same_selected_box(slot, original_box) and i < len(self._combo_hand):
                            self._combo_hand[i] = name
            if guard_active:
                resolved = self._resolve_selected_rule_turns(context, selected_frame, box, name, remaining)
                if resolved is None:
                    return self._recover_card_selection(context)
                selected_frame, box, remaining = resolved
            if guard_active and self._held_until_late(name, remaining):
                return self._defer_held_card(context, name, box, remaining)
            if self._drinks.pending_card and name != self._drinks.pending_card:
                logger.warning("喝药后目标牌名称未确认，解除旧目标；按当前已确认的选中牌继续")
                self._drinks.pending_card = None
                skip_named = True
            if self._drink_step(context, selected_frame, card_name=None if skip_named else name):
                self.start_time = time.time()
                return not self._drink_error
            self._drinks.pending_card = None
            self._drinks.inspection_streak = 0
            self._drinks.opening = False
        if context.tasker.stopping:
            return True
        # OCR/drink checks can span an animation. Re-localize immediately
        # before committing; never click the old card position after it moved.
        for attempt in range(3):
            if context.tasker.stopping:
                return True
            current = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, current):
                self._disconnect_detected = True
                return True
            current_box = CardNameReader().locate_raised(context, current, original_box)
            if CardNameReader.same_selection(selected_frame, box, current, current_box):
                break
            if attempt < 2:
                time.sleep(0.15)
        else:
            logger.info("连续复核仍未确认原选中牌，重新观察当前手牌")
            return self._recover_card_selection(context)
        box = current_box
        if getattr(self, "_drinks", None) is not None and hasattr(self._drinks, "turns"):
            remaining = self._drinks.turns.read(context, current)
            resolved = self._resolve_selected_rule_turns(context, current, box, name, remaining)
            if resolved is None:
                return self._recover_card_selection(context)
            current, box, remaining = resolved
            if self._held_until_late(name, remaining):
                return self._defer_held_card(context, name, box, remaining)
        context.tasker.controller.post_click(
            box[0] + box[2] // 2,
            box[1] + box[3] // 2,
        ).wait()
        submitted = self._confirm_card_submission(context, current, box)
        logger.info("出牌点击已观察 耗时:{:.2f}秒".format(time.time() - self.start_time))
        if not submitted or self._disconnect_detected or context.tasker.stopping:
            self.start_time = time.time()
            return True
        self._combo.submitted(name)
        self._held_selection = None
        self._held_reselections = 0
        self._selection_failures = 0
        playable = self._wait_until_playable(context, require_hand_stable=True)
        if playable and not self._disconnect_detected and not context.tasker.stopping and self._post_play_delay > 0:
            time.sleep(self._post_play_delay)
        self.start_time = time.time()

        return True

    @staticmethod
    def _handle_move_cards(context: Context, image=None) -> bool:
        """
        一种处理移动卡片界面的笨方法

        Args:
            context: maa的Context类
            image: 截图

        Returns:
            bool: 如果出现了移动卡牌界面并处理成功，返回True；
                如果没有出现移动卡牌界面或处理过程出现问题，返回False。
        """
        if image is None:
            image = context.tasker.controller.post_screencap().wait().get()

        reco_detail = context.run_recognition("ProduceRecognitionChooseMoveCards", image)
        if reco_detail.hit:
            y = 450
            while y < 1100:
                # for x in [140, 285, 440, 585]:
                for x in [140, 285]:
                    context.tasker.controller.post_click(x, y).wait()
                    time.sleep(0.2)
                    context.tasker.controller.post_click(x, y).wait()
                    time.sleep(0.2)
                image = context.tasker.controller.post_screencap().wait().get()
                reco_detail = context.run_recognition("ProduceRecognitionChooseMoveCards", image)
                if not reco_detail.hit:
                    context.run_task("ProduceMoveCards")
                    return True
                else:
                    y = y + 100
                # 检测任务中止的情况，防止卡死，检测成功时返回False
                if context.tasker.stopping:
                    return False
        return False

    def _recheck_battle_frame(self, context):
        """Recheck a missed HUD without clicking or resetting this battle session."""
        for _ in range(2):
            if context.tasker.stopping:
                return None
            time.sleep(0.25)
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            disconnect_node = shared._detect_disconnect_screen(context, image)
            if disconnect_node:
                logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
                self._disconnect_detected = True
                return None
            if self._is_playing_card(context, image):
                return image
        return None

    @staticmethod
    def _is_playing_card(context: Context, image=None) -> bool:
        """
        判断是否处于出牌场景

        Args:
            context: maa的Context类
            image: 截图

        Returns:
            bool: 如果处于出牌场景，返回True；否则返回False。
        """
        if image is None:
            image = context.tasker.controller.post_screencap().wait().get()

        reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", image)
        if reco_detail and reco_detail.hit:
            return True
        # reco_detail = context.run_recognition('ProduceRecognitionChooseMoveCards', image)
        # if reco_detail and reco_detail.hit:
        #     return True
        return False

    def _wait_until_playable(self, context: Context, confirmation_count=1, *, require_hand_stable=False):
        """
        等待直到处于可出牌状态

        Args:
            context: maa的Context类
            confirmation_count：重复核对的次数，用来应对识别对象一闪而过的假True情况（主要存在于喝饮料的时候）

        Returns:
            bool: 处于出牌场景时，返回True；不处于出牌场景时，返回False
        """
        count_playable = 0
        count_exit = 0
        previous_hand = None
        drink_recovery = confirmation_count > 1
        wait_deadline = time.monotonic() + (4 if drink_recovery else 30)
        while True:
            if context.tasker.stopping:
                return False
            if time.monotonic() >= wait_deadline:
                if drink_recovery:
                    logger.warning("饮料使用后 4 秒内未恢复出牌，进入弹窗恢复检查")
                else:
                    logger.error("等待出牌页面超过 30 秒，保留现场")
                return False
            image = context.tasker.controller.post_screencap().wait().get()

            disconnect_node = shared._detect_disconnect_screen(context, image)
            if disconnect_node:
                self._disconnect_detected = True
                logger.warning(f"出牌等待阶段检测到断线界面 {disconnect_node}，交回全局恢复流程")
                return False

            # Transfer overlays show the blurred battle underneath. Handle
            # them before accepting SKIP, and never use the legacy blind
            # coordinate clicks for a configured National Idol combination.
            if combo_target(self._rule_preset()):
                from custom.battle_move import MovePicker, MoveDisconnected, is_move_page

                if is_move_page(context, image):
                    try:
                        MovePicker(context, self._rule_preset(), None).run()
                    except MoveDisconnected as exc:
                        self._disconnect_detected = True
                        logger.warning(str(exc))
                        return False
                    except InterruptedError:
                        if context.tasker.stopping:
                            return False
                        raise
                    except RuntimeError as exc:
                        logger.error(str(exc))
                        self._drink_error = True
                        return False
                    count_playable = 0
                    wait_deadline = time.monotonic() + (4 if drink_recovery else 30)
                    continue

            # 通过跳过回合按钮检测是否处于可出牌状态
            reco_detail = context.run_recognition("ProduceRecognitionSkipRound", image)
            if reco_detail and reco_detail.hit:
                # A blurred battle remains visible through drink details.
                # Never accept that background as a playable hand.
                from custom.battle_drinks import BattleDrinkSession

                cancel = BattleDrinkSession._cancel_box(context, image)
                if cancel:
                    count_playable = 0
                    # During consumption, leave modal recovery to close_detail.
                    # On entry/resume we own this stale modal and must close it;
                    # merely waiting here previously made the close path dead.
                    if not drink_recovery:
                        x, y, w, h = cancel
                        context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
                        time.sleep(0.35)
                    time.sleep(0.15)
                    continue
                if require_hand_stable:
                    from custom.battle_drinks import CardNameReader
                    from custom.hand_identity import hand_boxes

                    detail = context.run_recognition("ProduceRecognitionCards", image)
                    rows = list(detail.all_results or []) if detail and detail.hit else []
                    boxes = hand_boxes(rows)
                    flying = any(
                        row.label in ("cards", "suggestions", "useless")
                        and float(row.score) >= 0.5
                        and 120 <= row.box[2] <= 260
                        and 180 <= row.box[3] <= 350
                        and 300 <= row.box[1] < 740
                        for row in rows
                    )
                    selected = CardNameReader._panel_title_roi(image) is not None
                    empty = (
                        not boxes
                        and not flying
                        and not selected
                        and (no_cards := context.run_recognition("ProduceRecognitionNoCards", image))
                        and no_cards.hit
                    )
                    stable = (
                        (boxes or empty)
                        and previous_hand is not None
                        and len(boxes) == len(previous_hand)
                        and all(self._same_selected_box(a, b) for a, b in zip(boxes, previous_hand))
                    )
                    previous_hand = boxes if (boxes or empty) and not flying and not selected else None
                    if flying or selected or not stable:
                        time.sleep(0.18)
                        continue
                count_playable += 1
                if count_playable >= confirmation_count:
                    return True
            else:
                count_playable = 0

            if drink_recovery:
                # A fading drink modal briefly matches the generic cancel
                # button. Running ProduceButton re-recognizes it after it has
                # vanished and blocks for the pipeline's 20-second timeout.
                # Observe only here; failed use is recovered by close_detail.
                time.sleep(0.12)
                continue

            # 检测血条是否存在，如连续n次检查不到血条，则认为已退出出牌场景
            # 如果识别时间太长，2次就够了，主要避免CLEAR效果遮住血条的情况
            # 如果识别时间太短，导致提前出函数，CLEAR转PERFECT的那一回合出牌计时会差很远。如果出现这种情况，就设置为3次
            reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", image)
            if not (reco_detail and reco_detail.hit):
                count_exit += 1
                if count_exit >= 2:
                    return False
            else:
                count_exit = 0

            # 解决莫名其妙的误触问题
            reco_detail = context.run_recognition("ProduceButton", image)
            if reco_detail and reco_detail.hit:
                context.run_task("ProduceButton")

            # 处理移动卡片界面
            if self._handle_move_cards(context, image):
                count_playable = 0
                count_exit = 0

            # 检测任务中止的情况，防止卡死，检测成功时返回False
            if context.tasker.stopping:
                return False

            # 睡一会
            time.sleep(1)
