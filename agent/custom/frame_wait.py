"""基于画面变化的等待：滑动/点击后轮询截图，区域连续两帧不再变化就返回，并保留最长等待上限。

参考 MaaFramework 的 post_wait_freezes 与 MAA 拖动结束后的停留做法；返回最后一帧供调用方复用，
避免“固定 sleep 后再截一次图”。
"""

import time

import numpy as np


def _signature(frame, roi):
    if frame is None or getattr(frame, "ndim", 0) != 3:
        return None
    if roi is None:
        return frame[::4, ::4, :3].astype(np.int16)
    x, y, w, h = roi
    return frame[y : y + h : 4, x : x + w : 4, :3].astype(np.int16)


def wait_settled(context, roi=None, *, min_wait=0.2, max_wait=1.0, interval=0.12, threshold=2.0):
    """至少等 min_wait 秒，之后区域连续两帧平均像素差 < threshold 即返回该帧；最多等 max_wait 秒。

    任务停止时返回 None。
    """
    start = time.monotonic()
    time.sleep(min_wait)
    previous = None
    frame = None
    while True:
        if context.tasker.stopping:
            return None
        frame = context.tasker.controller.post_screencap().wait().get()
        current = _signature(frame, roi)
        if previous is not None and current is not None and previous.shape == current.shape:
            if float(np.abs(previous - current).mean()) < threshold:
                return frame
        previous = current
        if time.monotonic() - start >= max_wait:
            return frame
        time.sleep(interval)
