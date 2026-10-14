# HIF 识别素材

本目录与 `agent/custom/event_templates.py` 的 `HIF_TEMPLATES` 一一对应，大小写必须保持一致。

| 文件 | 游戏原始 Sprite | 用途 |
|---|---|---|
| Vo.png | icon_volesson | 公开课程麦克风轮廓 |
| Da.png | icon_dalesson | 公开课程鞋子轮廓 |
| Vi.png | icon_vilesson | 公开课程视觉图标轮廓 |
| lesson.png | icon_class_produce | 日程授业入口轮廓 |
| lesson_header.png | icon_class_produce | 授业选项页页眉轮廓 |
| sp.png | lesson_icon_sp | SP 标志的稳定中心部分 |

来源为本机 `E:\DMM\gakumas\gakumas_Data\data.unity3d` 中 `resources.assets` 的 Sprite，使用 UnityPy 1.25.4 只读提取。原始包哈希、Sprite path ID、缩放/裁剪方法和输出文件哈希见 `sources.json`。

前五张为 Alpha 轮廓模板，需要先将对应的 UI 颜色转换成黑底白色轮廓，再调用 MaaFramework TemplateMatch。它们不是直接对彩色截图匹配的模板。SP 使用普通彩色模板匹配。

初继续使用 `produce/*.png`，NIA 继续使用 `produce/NIA/*.png`；识别调用共用，素材和区域按场景分开。HIF 模板未完整命中时，保留文字加属性图标校验。
