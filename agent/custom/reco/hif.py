from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


@AgentServer.custom_recognition("ProduceHIFConsultationShop")
class ProduceHIFConsultationShop(CustomRecognition):
    def analyze(self, context, argv):
        from ..shop import consultation_grid_visible
        if consultation_grid_visible(argv.image):
            return CustomRecognition.AnalyzeResult(box=[66, 482, 588, 356], detail={"stage": "consultation_shop"})
        return None


@AgentServer.custom_recognition("ProduceHIFDelete")
class ProduceHIFDelete(CustomRecognition):
    def analyze(self, context, argv):
        flag = context.run_recognition("ProduceChooseDeleteFlag", argv.image, pipeline_override={
            "ProduceChooseDeleteFlag": {"template": ["produce/delete_icon.png"]}})
        return CustomRecognition.AnalyzeResult(box=list(flag.best_result.box), detail={"stage": "delete"}) if flag and flag.hit and flag.best_result else None


@AgentServer.custom_recognition("ProduceHIFStart")
class ProduceHIFStart(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import read_hif_start
        box = read_hif_start(context, argv.image)
        return CustomRecognition.AnalyzeResult(box=box, detail={"stage": "start_confirmation"}) if box else None


@AgentServer.custom_recognition("ProduceHIFChoices")
class ProduceHIFChoices(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import read_hif_choices

        choices = read_hif_choices(context, argv.image)
        if choices is None:
            return None
        stage, rows = choices
        return CustomRecognition.AnalyzeResult(box=rows[0][1], detail={"stage": stage})


@AgentServer.custom_recognition("ProduceHIFResume")
class ProduceHIFResume(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import read_hif_resume
        box = read_hif_resume(context, argv.image)
        return CustomRecognition.AnalyzeResult(box=box, detail={"stage": "cultivation_resume"}) if box else None


@AgentServer.custom_recognition("ProduceHIFClassEvent")
class ProduceHIFClassEvent(CustomRecognition):
    event_text = "授業"

    def analyze(self, context, argv):
        from ..hif import read_hif_events

        events = read_hif_events(context, argv.image, self.event_text)
        return CustomRecognition.AnalyzeResult(box=events[0][1], detail={"stage": "event"}) if events else None


@AgentServer.custom_recognition("ProduceHIFCourseEvent")
class ProduceHIFCourseEvent(ProduceHIFClassEvent):
    event_text = "レッスン"


@AgentServer.custom_recognition("ProduceHIFShopEntry")
class ProduceHIFShopEntry(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import read_hif_shop_entry

        box = read_hif_shop_entry(context, argv.image)
        return CustomRecognition.AnalyzeResult(box=box, detail={"stage": "shop_entry"}) if box else None


@AgentServer.custom_recognition("ProduceHIFIntervalShop")
class ProduceHIFIntervalShop(CustomRecognition):
    def analyze(self, context, argv):
        from ..shop import is_interval_shop
        if is_interval_shop(context, argv.image):
            return CustomRecognition.AnalyzeResult(box=[374, 690, 280, 170], detail={"stage": "interval_shop"})
        return None


@AgentServer.custom_recognition("ProduceHIFChange")
class ProduceHIFChange(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import read_hif_change

        boxes = read_hif_change(context, argv.image)
        return CustomRecognition.AnalyzeResult(box=boxes[0], detail={"stage": "destination"}) if boxes else None


@AgentServer.custom_recognition("ProduceHIFChangeSource")
class ProduceHIFChangeSource(CustomRecognition):
    def analyze(self, context, argv):
        from ..hif import is_hif_change_source

        if is_hif_change_source(context, argv.image):
            return CustomRecognition.AnalyzeResult(box=[60, 660, 600, 440], detail={"stage": "source"})
        return None
