"""Bounded, two-frame battle state recognition for optional card rules."""

import re
import unicodedata

from .frame_geometry import reference_frame
from .shop import ocr


class BattleStateReader:
    POINTER_ROI = [0, 220, 95, 85]
    POWER_ROI = [110, 238, 50, 46]
    POINTERS = {
        "全力": "hif/produce/hif_full_power_pointer.png",
        "強気": "hif/produce/hif_strong_pointer.png",
        "温存": "hif/produce/hif_conserve_pointer.png",
    }

    def __init__(self):
        self.previous = {}

    @staticmethod
    def required(preset):
        fields = set()
        for rule in preset["rules"]:
            if not rule["enabled"]:
                continue
            groups = list(rule.get("conditions", []))
            for override in rule.get("priorityOverrides", []):
                groups.extend(override["conditions"])
            fields.update(atom["field"] for group in groups for atom in group if atom["field"] != "remaining")
        return fields

    def pointer(self, context, image):
        matches = []
        for stance, template in self.POINTERS.items():
            result = context.run_recognition(
                "ProduceRecognitionPoint", image,
                pipeline_override={"ProduceRecognitionPoint": {
                    "recognition": "TemplateMatch", "template": template,
                    "roi": self.POINTER_ROI, "threshold": 0.8,
                }},
            )
            if result and result.hit:
                matches.append(stance)
        return matches[0] if len(matches) == 1 else None

    def full_power(self, context, image):
        values = set()
        for row in ocr(context, image, self.POWER_ROI, [r"^[0-9０-９]{1,3}$"]):
            text = unicodedata.normalize("NFKC", str(row.text)).strip()
            if float(row.score) >= 0.85 and re.fullmatch(r"[0-9]{1,3}", text):
                values.add(int(text))
        return next(iter(values)) if len(values) == 1 else None

    def read(self, context, image, fields):
        if not fields or not reference_frame(image):
            self.previous = {}
            return {}
        current = {}
        if "stance" in fields:
            current["stance"] = self.pointer(context, image)
        if "fullPower" in fields:
            current["fullPower"] = self.full_power(context, image)
        agreed = {
            field: value for field, value in current.items()
            if value is not None and value == self.previous.get(field)
        }
        self.previous = current
        return agreed
