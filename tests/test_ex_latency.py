"""Regressions for a visible memory card and post-deletion shop overlays."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from custom import memory_modes as m
from custom.action.produce import ProduceHIFDeleteAuto
from custom import memory_farming as f


class MemoryScanLatency(unittest.TestCase):
    def reader(self, heading=True):
        reader=object.__new__(m.MemoryReader)
        reader.context=Mock();reader.context.tasker.stopping=False
        image=np.zeros((1280,720,3),np.uint8)
        texts=[dict(text='獲得するメモリーを選択',box=[0,20,400,30])]
        if heading:texts.append(dict(text='獲得可能スキルカード',box=[30,330,300,30]))
        reader.capture=Mock(return_value=(image,texts));reader.scroll=Mock()
        return reader,image,texts

    def test_visible_unreadable_primary_does_not_scroll(self):
        r,_,_=self.reader();r.primary=Mock(return_value=None)
        with self.assertRaises(ValueError):r.scan('card')
        r.scroll.assert_not_called();self.assertEqual(r.primary.call_count,1)

    def test_visible_primary_is_read_only_twice_for_two_frame_check(self):
        r,_,_=self.reader();r.primary=Mock(return_value=m.Card('眠気',False,0))
        with patch.object(m.time,'sleep'):cards,_=r.scan('card')
        self.assertEqual(cards[0].name,'眠気');self.assertEqual(r.primary.call_count,2)
        r.scroll.assert_not_called()

    def test_animated_background_cannot_force_four_top_swipes(self):
        r,image,texts=self.reader(False);r.primary=Mock(return_value=None)
        changed=image.copy();changed[:250]=255;changed[:,600:]=255
        r.capture.side_effect=[(image,texts),(changed,texts)]
        with self.assertRaises(ValueError):r.scan('card')
        self.assertEqual(r.scroll.call_count,1)

    def test_candidate_switch_during_scroll_yields(self):
        r,image,texts=self.reader(False);r.primary=Mock(return_value=None)
        r.capture.side_effect=[(image,texts),(image,[])]
        with self.assertRaises(m.MemoryTransition):r.scan('card')
        self.assertEqual(r.scroll.call_count,1)

    def test_delete_resume_closes_bubble_before_waiting_for_prices(self):
        ctx=Mock();ctx.tasker.stopping=False
        a=ProduceHIFDeleteAuto();order=[]
        a._delete_from_deck=Mock(return_value='deleted')
        a._dismiss_comment_bubble=Mock(side_effect=lambda c:order.append('bubble') or True)
        a._reenter_shop=Mock(side_effect=lambda c:order.append('grid') or False)
        with patch('custom.shop.ocr',return_value=[]),patch('custom.action.produce._detect_disconnect_screen',return_value=False),patch('custom.action.produce._load_card_lists',return_value={}):
            a.run(ctx,NS())
        self.assertEqual(order,['bubble','grid'])

    def test_diamond_bottom_uses_stable_text_despite_animation(self):
        r,image,texts=self.reader(False)
        texts += [dict(text='アビリティ',box=[70,400,100,25])]+[
            dict(text=s,box=[110,460+i*90,260,25]) for i,s in enumerate(['初期ボーカル上昇+15','初期ビジュアル上昇+10','初期Pポイント+30'])]
        changed=np.full_like(image,190)
        r.capture.side_effect=[(image,texts),(changed,list(reversed(texts)))]
        with patch.object(f,'diamond_row',return_value=False):self.assertFalse(f.read_diamond(r,'picker'))
        self.assertEqual(r.scroll.call_count,1)

    def test_diamond_new_frame_checked_before_bottom_return(self):
        r,image,texts=self.reader(False)
        texts.append(dict(text='アビリティ',box=[70,400,100,25]))
        r.capture.return_value=(image,texts)
        with patch.object(f,'diamond_row',side_effect=[False,True]):self.assertTrue(f.read_diamond(r,'picker'))
        self.assertEqual(r.scroll.call_count,1)

    def test_diamond_rows_moving_are_not_end_of_scroll(self):
        before=[('card A',400),('card B',500),('card C',600)]
        self.assertFalse(f.same_content(before,[(t,y-90) for t,y in before]))
        self.assertFalse(f.same_content([],[]))

if __name__=='__main__':unittest.main()
