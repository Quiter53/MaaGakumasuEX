import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"agent"))
from custom.hand_identity import HandIdentityReader, hand_boxes, visible_mask


class HandIdentityTests(unittest.TestCase):
    def reader(self):
        reader=HandIdentityReader()
        reader.gates={m:dict(score=.4,margin=.1) for m in ("full","local")}
        return reader

    def test_three_full_four_five_local(self):
        image=np.zeros((1280,720,3),np.uint8)
        reader=self.reader()
        for xs,expected in (([48,264,481],"full"),([19,184,348,512],"local"),([19,142,266,389,512],"local")):
            boxes=[[x,884,189,249] for x in xs]
            with patch.object(reader,"rank",return_value=("test",.8,.3)) as rank:
                result=reader.read(image,boxes[0],boxes)
                self.assertEqual(expected,result.mode)
                self.assertEqual(expected,rank.call_args.args[2])

    def test_overlap_even_when_detector_misses_card(self):
        reader=self.reader()
        boxes=[[19,884,189,249],[184,884,189,249],[512,884,189,249]]
        with patch.object(reader,"rank",return_value=("test",.8,.3)):
            self.assertEqual("local",reader.read(np.zeros((1280,720,3),np.uint8),boxes[0],boxes).mode)

    def test_neighbour_mask_and_cost_regions(self):
        box=[19,884,189,249]
        mask=visible_mask(box,[box,[142,884,189,249]])
        self.assertFalse(mask[:,21:].any())
        self.assertFalse(mask[:9,:17].any())
        self.assertGreater(mask.sum(),120)

    def test_identity_requires_gate_and_margin(self):
        reader=self.reader()
        image=np.zeros((1280,720,3),np.uint8)
        box=[48,884,189,249]
        with patch.object(reader,"rank",return_value=("wrong",.99,.01)):
            self.assertIsNone(reader.read(image,box,[box]).name)
        reader.cache.clear()
        reader.gates=None
        with patch.object(reader,"rank",return_value=("wrong",.99,.9)):
            self.assertIsNone(reader.read(image,box,[box]).name)

    def test_cache_invalidated_by_pixels_or_occlusion(self):
        reader=self.reader()
        image=np.zeros((1280,720,3),np.uint8)
        box=[48,884,189,249]
        with patch.object(reader,"rank",return_value=("test",.8,.3)) as rank:
            reader.read(image,box,[box]); reader.read(image,box,[box])
            self.assertEqual(1,rank.call_count)
            image[884:1099,48:237]=50
            reader.read(image,box,[box])
            self.assertEqual(2,rank.call_count)
            reader.read(image,box,[box,[170,884,189,249]])
            self.assertEqual(3,rank.call_count)

    def test_unusable_included_duplicate_fragment_excluded(self):
        rows=[NS(label=label,box=box,score=score) for label,box,score in [
            ("cards",[19,884,189,249],.9),
            ("suggestions",[20,885,188,248],.8),
            ("useless",[184,884,189,249],.9),
            ("cards",[347,884,45,249],.9)]]
        self.assertEqual([[19,884,189,249],[184,884,189,249]],hand_boxes(rows))

    def test_raise_animation_and_fragment_rejected(self):
        reader=self.reader()
        image=np.zeros((1280,720,3),np.uint8)
        self.assertIsNone(reader.read(image,[10,884,50,249],[[10,884,50,249]]))
        self.assertIsNone(reader.read(image,[19,884,189,249],[[19,884,189,249],[184,700,189,249]]))
        self.assertIsNone(reader.read(image,[19,884,189,249],[[184,884,189,249]]))


if __name__=="__main__": unittest.main()


def test_overwide_confident_hand_box_is_trimmed_to_card_width():
    from types import SimpleNamespace as NS

    from custom.frame_geometry import trim_overwide_hand_box
    from custom.hand_identity import hand_boxes

    # 合成评估中检测器把最右侧牌框延伸到背景亮部：左边缘准确，宽 221–260。
    assert trim_overwide_hand_box([481, 884, 222, 247], 0.96) == [481, 884, 188, 247]
    assert trim_overwide_hand_box([49, 881, 236, 248], 0.95) == [49, 881, 188, 248]
    # 正常牌、两张牌合并、低置信背景误检、异常高度都不处理。
    assert trim_overwide_hand_box([264, 884, 189, 248], 0.96) == [264, 884, 189, 248]
    assert trim_overwide_hand_box([19, 884, 320, 247], 0.96) == [19, 884, 320, 247]
    assert trim_overwide_hand_box([424, 861, 260, 256], 0.46) == [424, 861, 260, 256]
    assert trim_overwide_hand_box([420, 859, 244, 284], 0.96) == [420, 859, 244, 284]
    rows = [NS(label="cards", box=[48, 884, 187, 248], score=0.96), NS(label="cards", box=[478, 883, 221, 244], score=0.96)]
    assert hand_boxes(rows) == [[48, 884, 187, 248], [478, 883, 188, 244]]
