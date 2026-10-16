import numpy as np
from utils import logger
from maa.custom_action import CustomAction
from custom.frame_geometry import reference_frame
from custom.produce.memory import modes as modes
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


@AgentServer.custom_recognition('ProduceMemoryRecognition')
class ProduceMemoryRecognition(CustomRecognition):
    def analyze(self,context,argv):
        if not modes.SESSION.enabled:return None
        image=argv.image
        # Small capture-border changes must not hide the result screen.
        # Require the actual panel/color/text evidence below.
        if not reference_frame(image):return None
        # The interactive MEMORY reveal has a saturated cyan background,
        # so the neutral summary-panel gate below intentionally misses it.
        a=image[30:150:8,60:660:8,:3].astype(np.int16)
        if ((a[:,:,0]>100)&(a[:,:,1]>70)&(a[:,:,2]<100)).mean()>.6:
            kind=modes.screen_kind(modes.ocr(context,image))
            if kind in ('generation','reveal'):
                return CustomRecognition.AnalyzeResult(box=[0,0,1,1],detail={'memory':kind})
        # Result toolbar / candidate header are broad neutral panels. This
        # cheap gate avoids three OCR calls on every ordinary battle frame.
        def neutral(roi):
            x,y,w,h=roi;a=image[y:y+h:4,x:x+w:4,:3].astype(np.int16)
            return float(((a.max(2)-a.min(2)<20)&(a.mean(2)>170)).mean())
        rois=[]
        if neutral([0,410,720,65])>.75:rois.append([0,390,image.shape[1],100])
        if neutral([60,30,600,115])>.75:rois.append([60,30,600,115])
        # The confirmation sheet's orange title, including resumed tasks.
        a=image[875:925:4,60:620:4,:3].astype(np.int16)
        if ((a[:,:,2]>220)&(a[:,:,1]>100)&(a[:,:,1]<220)&(a[:,:,0]<150)).mean()>.4:
            rois.append([0,830,image.shape[1],180])
        for roi in rois:
            text=modes.text_join(modes.ocr(context,image,roi))
            if any(s in text for s in ('メモリー変換','獲得するメモリー','メモリー確定確認','メモリー再生成','回忆转换','选择要获得的回忆','回忆确定确认','回忆再生成')):
                kind=modes.screen_kind(modes.ocr(context,image))
                if kind in ('summary','picker','confirm','reroll_confirm'):
                    return CustomRecognition.AnalyzeResult(box=[0,0,1,1],detail={'memory':kind})
        return None


@AgentServer.custom_action('ProduceMemoryInspect')
class ProduceMemoryInspect(CustomAction):
    def run(self,context,argv):
        try:return modes.handle_memory(context)
        except InterruptedError:return True
        except Exception as error:
            logger.exception(f'回忆处理异常，保留候选状态并交回页面识别：{error}')
            return True


@AgentServer.custom_action('ProduceMemoryBackHome')
class ProduceMemoryBackHome(CustomAction):
    def run(self,context,argv):return modes.go_home(context)
