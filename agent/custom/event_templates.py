"""Shared Maa template matching; each scenario owns its image paths and ROI."""

HIF_TEMPLATES = {
    "Vo": "produce/HIF/Vo.png",
    "Da": "produce/HIF/Da.png",
    "Vi": "produce/HIF/Vi.png",
    "class": "produce/HIF/lesson.png",
    "class_header": "produce/HIF/lesson_header.png",
    "SP": "produce/HIF/sp.png",
}


def match_event_template(context, image, template, roi, *, node="ProduceRecognitionEvent", **params):
    return context.run_recognition(
        node,
        image,
        pipeline_override={
            node: {
                "recognition": "TemplateMatch",
                "template": template,
                "roi": roi,
                **params,
            }
        },
    )


def template_results(detail, threshold):
    """Native all_results includes below-threshold diagnostics; never click those."""
    if not detail or not detail.hit:
        return []
    rows = getattr(detail, "filtered_results", None)
    if not isinstance(rows, (list, tuple)):
        return []
    valid = []
    for row in rows:
        box, score = getattr(row, "box", None), getattr(row, "score", None)
        if isinstance(box, (list, tuple)) and len(box) == 4 and isinstance(score, (int, float)) and score >= threshold:
            if not any(abs(box[0] - old.box[0]) < 20 and abs(box[1] - old.box[1]) < 20 for old in valid):
                valid.append(row)
    return valid
