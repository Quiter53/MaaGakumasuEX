"""Shared drag-and-hold input for scrollable cultivation lists.

Callers own the pane coordinates, overlap distance and page verification.
Carousel page turns and sliders deliberately do not use this list gesture.
"""


def scroll_list(context, begin, end):
    from maa.pipeline import JSwipe

    if context.tasker.stopping:
        return False
    result = context.run_action_direct("Swipe", JSwipe(begin=[*begin, 1, 1], end=[[*end, 1, 1]], duration=[900], end_hold=[350]))
    return bool(result and result.success and not context.tasker.stopping)
