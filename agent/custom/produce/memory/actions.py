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
        flow=getattr(modes.SESSION,'flow',None)
        if flow is not None and not flow.holding:
            return None  # A positively identified destination owns the page.
        if flow is not None and flow.holding:
            # The action owns an unfinished settlement even during a fade.
            # Positive destination/network checks happen in the bounded action.
            return CustomRecognition.AnalyzeResult(box=[0,0,1,1],detail={'memory':'pending'})
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
        for roi in modes.screen_title_regions(image):
            rows=modes.ocr(context,image,roi)
            if modes.has_memory_title(rows):
                kind=modes.screen_kind(modes.screen_ocr(context,image,title=(roi,rows)))
                if kind in ('summary','picker','confirm','reroll_confirm'):
                    return CustomRecognition.AnalyzeResult(box=[0,0,1,1],detail={'memory':kind})
                # A locally confirmed panel with incomplete tab/footer OCR is
                # retried by the memory handler, not generic Next/Decide.
                return CustomRecognition.AnalyzeResult(box=[0,0,1,1],detail={'memory':'pending'})
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
