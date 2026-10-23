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

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        idol_name = json.loads(argv.custom_recognition_param)["idol_name"]
        song_name = json.loads(argv.custom_recognition_param)["song_name"]
        recognized_name = ""
        recognized_song = ""

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
        if true_end_detail and true_end_detail.hit:
            logger.debug("识别到True End")
            idol_name_roi = [440, 128, 280, 64]
            song_name_roi = [380, 90, 320, 45]
        else:
            logger.debug("未识别到True End")
            idol_name_roi = [400, 98, 320, 64]
            song_name_roi = [340, 60, 380, 45]

        name_detail = context.run_recognition(
            "ProduceChooseIdolName",
            argv.image,
            pipeline_override={"ProduceChooseIdolName": {"recognition": "OCR", "roi": idol_name_roi}},
        )
        if name_detail and name_detail.hit:
            recognized_name = "".join([item.text for item in name_detail.all_results]).replace(" ", "")
            logger.info(f"识别到偶像名称: {recognized_name}，相似度: {self.similarity_ratio(recognized_name, idol_name):.2f}")

        song_detail = context.run_recognition(
            "ProduceChooseIdolSong",
            argv.image,
            pipeline_override={"ProduceChooseIdolSong": {"recognition": "OCR", "roi": song_name_roi}},
        )
        if song_detail and song_detail.hit:
            recognized_song = "".join([item.text for item in song_detail.all_results]).replace("[", "").replace("]", "")
            logger.info(f"识别到歌曲名称: {recognized_song}，相似度: {self.similarity_ratio(recognized_song, song_name):.2f}")

        if self.similarity_ratio(recognized_name, idol_name) >= 0.7 and self.similarity_ratio(recognized_song, song_name) >= 0.7:
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "识别偶像卡成功"})
        else:
            return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "识别偶像卡失败"})

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
        context.run_action("Click_1")
        cards_reco_detail = context.run_recognition("ProduceRecognitionCards", argv.image)
        health_reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", argv.image)
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
