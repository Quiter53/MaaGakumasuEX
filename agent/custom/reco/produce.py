import os
import json
import time
import unicodedata
from typing import Tuple, Union, Optional
from difflib import SequenceMatcher

from utils import logger
from maa.define import RectType
from maa.context import Context
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


@AgentServer.custom_recognition("ProduceChooseIdolAuto")
class ProduceChooseIdolAuto(CustomRecognition):
    """
    自动识别当前偶像名称和歌曲
    """

    # Name/song positions without and with the True End banner.
    IDENTITY_LAYOUTS = (
        ([400, 98, 320, 64], [340, 60, 380, 45]),
        ([440, 128, 280, 64], [380, 90, 320, 45]),
    )

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        params = json.loads(argv.custom_recognition_param)
        idol_name, song_name = params["idol_name"], params["song_name"]

        true_end_detail = context.run_recognition(
            "ProduceChooseIdolTrueEnd",
            argv.image,
            pipeline_override={
                "ProduceChooseIdolTrueEnd": {
                    "recognition": "OCR",
                    "expected": ["True", "End"],
                    "roi": [430, 34, 266, 48],
                }
            },
        )
        preferred = int(bool(true_end_detail and true_end_detail.hit))
        for layout in (preferred, 1 - preferred):
            name_roi, song_roi = self.IDENTITY_LAYOUTS[layout]
            fields = []
            for node, roi in (("ProduceChooseIdolName", name_roi), ("ProduceChooseIdolSong", song_roi)):
                detail = context.run_recognition(
                    node, argv.image, pipeline_override={node: {"recognition": "OCR", "roi": roi}},
                )
                fields.append(self._text_lines(detail))
            logger.debug(f"偶像卡识别布局={layout} 名称区域={fields[0]} 歌曲区域={fields[1]}")
            # A missed banner can place both title and name inside Name ROI.
            # Keep the rows separate and require a complete pair in one layout.
            if self._matches_lines(fields, idol_name, song_name):
                logger.info(f"识别偶像卡成功：{idol_name} / {song_name}")
                return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "识别偶像卡成功"})
        return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "识别偶像卡失败"})

    @staticmethod
    def _text_lines(detail):
        """Join horizontal OCR fragments, never different title/name rows."""
        if not detail or not detail.hit:
            return []
        rows = [(item.text, getattr(item, "box", None)) for item in detail.all_results or [] if item.text.strip()]
        if any(box is None for _, box in rows):
            return rows
        rows = [(text, list(box)) for text, box in rows if len(box) == 4 and box[2] > 0 and box[3] > 0]
        groups = []
        for text, box in sorted(rows, key=lambda item: (item[1][1], item[1][0])):
            if groups:
                previous = groups[-1][0][1]
                overlap = min(box[1] + box[3], previous[1] + previous[3]) - max(box[1], previous[1])
                if overlap >= min(box[3], previous[3]) * 0.6:
                    groups[-1].append((text, box))
                    continue
            groups.append([(text, box)])
        lines = []
        for group in groups:
            left = min(box[0] for _, box in group)
            top = min(box[1] for _, box in group)
            right = max(box[0] + box[2] for _, box in group)
            bottom = max(box[1] + box[3] for _, box in group)
            lines.append(("".join(text for text, _ in sorted(group, key=lambda item: item[1][0])), [left, top, right - left, bottom - top]))
        return lines

    @classmethod
    def _matches_pair(cls, name, song, idol_name, song_name):
        # 筱 is an observed OCR spelling of 篠 in an idol name only.
        name_score = cls.similarity_ratio(name.replace("筱", "篠"), idol_name.replace("筱", "篠"))
        normalized_song, expected_song = cls.normalize_text(song), cls.normalize_text(song_name)
        song_matches = normalized_song == expected_song if len(expected_song) <= 3 else cls.similarity_ratio(song, song_name) >= 0.7
        return name_score >= 0.7 and bool(expected_song) and song_matches

    @classmethod
    def _matches_lines(cls, fields, idol_name, song_name):
        lines = fields[0] + fields[1]
        # Older callers without geometry still provide one complete line per
        # field. They cannot use cross-field/layout recovery.
        if lines and all(box is None for _, box in lines):
            return len(fields[0]) == len(fields[1]) == 1 and cls._matches_pair(fields[0][0][0], fields[1][0][0], idol_name, song_name)
        for name_roi, song_roi in cls.IDENTITY_LAYOUTS:
            candidates = []
            for roi in (name_roi, song_roi):
                x, y, w, h = roi
                candidates.append({
                    cls.normalize_text(text): (text, box) for text, box in lines
                    if box is not None and len(box) == 4 and x - 3 <= box[0] and y - 3 <= box[1]
                    and box[0] + box[2] <= x + w + 3 and box[1] + box[3] <= y + h + 3
                    and cls.normalize_text(text)
                })
            if len(candidates[0]) != 1 or len(candidates[1]) != 1:
                continue
            name, name_box = next(iter(candidates[0].values()))
            song, song_box = next(iter(candidates[1].values()))
            gap = name_box[1] - (song_box[1] + song_box[3])
            # Adjacent rows may overlap slightly in OCR boxes. A whole-row
            # gap belongs to different layouts, not one card identity pair.
            if -6 <= gap <= 20 and cls._matches_pair(name, song, idol_name, song_name):
                return True
        return False

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize equivalent OCR glyphs without lowering the match threshold."""
        return "".join(char for char in unicodedata.normalize("NFKC", text) if char.isalnum())

    @staticmethod
    def similarity_ratio(str1, str2):
        """Compare names after full-width, unit and punctuation normalization."""
        first = ProduceChooseIdolAuto.normalize_text(str1)
        second = ProduceChooseIdolAuto.normalize_text(str2)
        # Missing names or punctuation alone are never an identity match.
        return SequenceMatcher(None, first, second).ratio() if first and second else 0.0


@AgentServer.custom_recognition("ProduceShowStart")
class ProduceShowStart(CustomRecognition):
    """
    检测通过屏幕是否旋转判断演出开始
    """

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        image = argv.image
        height, width = image.shape[0], image.shape[1]
        if height < width:
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "屏幕旋转"})
        return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "屏幕未旋转"})


@AgentServer.custom_recognition("ProduceShowEnd")
class ProduceShowEnd(CustomRecognition):
    """
    检测通过屏幕是否旋转判断演出结束
    """

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        image = argv.image
        height, width = image.shape[0], image.shape[1]
        if height > width:
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "屏幕旋转"})
        return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "屏幕未旋转"})


@AgentServer.custom_recognition("ProduceCardsFlagAuto")
class ProduceCardsFlagAuto(CustomRecognition):
    """
    自动识别出牌场景
    """

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        if argv.image.shape[1] > argv.image.shape[0]:
            return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "横屏过场"})
        # 先跑带 ROI 的体力槽模板（约 1 ms），只有命中时才跑出牌检测模型，
        # 非出牌页面每轮主循环省一次整帧 ONNX 推理；两者都命中才判定，结果与原先一致。
        health_reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", argv.image)
        cards_reco_detail = (
            context.run_recognition("ProduceRecognitionCards", argv.image) if health_reco_detail and health_reco_detail.hit else None
        )
        if cards_reco_detail and cards_reco_detail.hit and health_reco_detail and health_reco_detail.hit:
            logger.success("事件: 出牌场景")
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "识别到出牌场景"})
        else:
            return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "未识别到选择场景"})


@AgentServer.custom_recognition("ProduceLandscapeWait")
class ProduceLandscapeWait(CustomRecognition):
    def analyze(self, context, argv):
        landscape = argv.image.shape[1] > argv.image.shape[0]
        return CustomRecognition.AnalyzeResult(
            box=[0, 0, 1, 1] if landscape else None,
            detail={"landscape": landscape},
        )


@AgentServer.custom_recognition("ProduceCloseOwnedDeck")
class ProduceCloseOwnedDeck(CustomRecognition):
    """Resume safely if a stopped inspection left the owned-card drawer open."""

    def analyze(self, context, argv):
        from ..owned_deck import close_box, drawer_present

        if not drawer_present(context, argv.image):
            return None
        return close_box(context, argv.image)
