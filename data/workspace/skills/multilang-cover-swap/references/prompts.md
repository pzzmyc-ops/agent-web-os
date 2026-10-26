# prompt 原文（gpt-image / openai-images，operation=edit）

调用端点（同步，等上游出图）：

```bash
curl -s -X POST http://127.0.0.1:80/api/v1/media/invoke \
  -H "Content-Type: application/json" -H "Authorization: Bearer <KEY>" \
  -d '{"model":"openai-images","operation":"edit","input":{
        "prompt":"<下面任一段>","size":"1024x1520","quality":"high",
        "image":[{"base64":"<参考图 b64>","mime_type":"image/png"}]}}'
```
返回 `{"ok":true,...,"data":{"paths":["D:/mafagent/data/workspace/media/openai-images/xxxx.png"]}}`，
路径原样交给 `render_media`。

约束：`size` 必须是 16 的倍数或 `auto`，且**总像素别低于约 50 万**；一次最多 16 张参考图。

---

## 1. 抹字（第 1 步，**整图送，不裁切**）

```
This is a vertical poster image. Your output must have exactly the same framing, crop and composition.

Task: erase the title lettering completely, and nothing else. Remove the hand-written script title lines
and the thin decorative swash stroke, including their soft drop shadows, so that no letters or parts of
letters remain anywhere. Nothing else about the picture may change.

Strictly preserve: the exact same crop and framing, the same amount of empty background where the title
was, the same figures at exactly the same size, position, pose, clothing and lighting, the same background
colour with its gradient, the same colours and grain.

Fill the erased areas so they blend perfectly into their surroundings: flat background where there was
background, and a natural continuation of the hair where a letter stroke crossed it.

Do not zoom in, do not crop, do not re-frame, do not move or re-pose the figures, do not add anything new.
Output the same poster with no text anywhere.
```

要点：
- **整图直接送**。不要"裁出标题区域→抹掉→再贴回"，那条路上下完全对不上（残影 + 接缝错位）。
- 有装饰件时，在 Task 里**逐个点名**：`the small golden crown, the two pink hearts, the two white cat ears,
  the pink paw print, the pink ribbon bow with the golden bell, the two red boxing gloves, all the long thin
  swash strokes`，并写 `Fill every erased area so it blends perfectly into its surroundings.`
- 有要保留的元素（唇印、场景里的黑拳套/猫爪手套/猫耳发箍）也点名：`Strictly KEEP, unchanged and in exactly
  the same place: ...`
- 被安全审核拦（`safety_violations=[sexual]`）时：**重试**、**换措辞**（别强调"人物姿势保持不变"这类描述）、
  隔一会儿再试；仍不行就换 `wan2.70-image-pro` / `seedream`（见 SKILL.md 的「安全审核退路」）。
- 产物自检：`view_image` 看有没有残留笔画；"无字底图"里不该有任何字母残影。

---

## 2. 花字（第 2 步：在纯色底上写目标语言标题）

`{bg}` 填**与字色相反的底色**（见 SKILL.md 的表），`{lang}` 语言名，`{l1}`/`{l2}` 各行文字，`{spec1}` 颜色分工。

```
Look at the hand-drawn lettering in this reference image and study its style closely: glossy calligraphic
brush script, thick-and-thin pressure, long flowing swash tails, a slight italic slant, cream-white letters
with a smooth bevel, plus one word in dark glossy red, and thin red swash strokes sweeping across.

Now render ONLY the following new {lang} text in that exact same lettering style, on a completely flat,
uniform, solid {bg} background. The background must be perfectly plain with no gradient, no texture, no
vignette, and must not resemble any of the letter colours.

Line 1: {l1}    -> {spec1}, exactly like the reference's first line.
Line 2: {l2}    -> write all of it in cream white, exactly like the reference's second line.

Keep the same two-line staggered composition as the reference: line 1 in the upper right part of the frame,
line 2 lower and starting further to the left, the two lines overlapping slightly. Keep the same letter size,
the same line spacing, the same placement and margins inside the frame, and the same long thin red swash
strokes as in the reference.

Requirements: no drop shadow, no glow, no outline, no extra words, no logo, no watermark, no border, no frame.
Just the lettering and the red swashes on the flat background.
```

`{spec1}` 两种写法：
- 两段式：`write "Meu" in cream white and "Mentor" in dark glossy red`
- 整行一个词：`write all of this line in dark glossy red`

**字号比例要显式约束**（否则副行会被写成小字幕），实测有效：
```
IMPORTANT PROPORTIONS (look at the reference): the second line is a LARGE line - about as wide as the whole
title block (roughly 85-90% of the frame width) and its capital letters are about 35-40% as tall as the
capitals of line 1. Give it generous letter-spacing. Do not make line 2 a small subtitle.
```

**要柔光的设计**：prompt 里写 `NO outer glow, no drop shadow, crisp edges on the flat background`，
柔光留给第 3 步的 `--glow-op` 补——比让模型画光更好抠。

---

## 3. 非拉丁文字（日/韩/泰/中/阿拉伯…，**必须**多喂一张字形参考图）

不喂参考图时模型会自己"编字形"：实测泰语整行是乱码、韩语笔画被拉成拉丁字母。
先用系统字体把正确文字排成白底黑字图（PIL + `C:/Windows/Fonts/` 下的字体），作为**第 2 张参考图**传入。

```
You are given two reference images.
Image 1 shows the lettering STYLE to copy: ...
Image 2 shows, in plain black type on white, the EXACT characters you must write. Copy the characters
themselves faithfully - same characters, same spelling, no substitutions, no invented or distorted glyphs,
no missing or extra marks.

Now render the text of image 2 in the brush-script style of image 1, on a completely flat, uniform, solid
{bg} background.

Line 1: {l1}    -> {spec1}, in the style of image 1.
Line 2: {l2}    -> write all of it in cream white, in the style of image 1.

Keep the same two-line staggered composition as image 1 ...
Requirements: every character must be correctly formed and clearly readable; ...
```

泰语要额外强调（否则上下母音/声调符会被写花）：
```
IMPORTANT for Thai script: the Thai letters, the vowel signs above and below the letters, and the tone marks
must all stay CLEAN, SEPARATE and CORRECTLY PLACED - exactly as in image 2. Do not let the brush style
scatter, merge or distort the small vowel and tone marks, and do NOT invent extra marks.
```

字形参考图排版样例：

```python
from PIL import Image, ImageDraw, ImageFont
f = ImageFont.truetype("C:/Windows/Fonts/LeelawUI.ttf", 120)   # 泰语；韩语 malgun.ttf，
# 日语 YuGothR.ttc，繁体中文 msjh.ttc，简体 msyh.ttc
img = Image.new('RGB', (1500, 800), 'white'); d = ImageDraw.Draw(img)
d.text((60, 120), "เมนเทอร์ของฉัน", font=f, fill=(0, 0, 0))
d.text((60, 420), "อัศวินขาวของฉัน", font=f, fill=(0, 0, 0))
img.save('fontref.png')
```

- 副作用：模型会往这张参考的笔画靠，那一行容易偏"印刷体"；需要更"手写"就在 prompt 里
  再强调笔刷/连笔，或者接受它并跟用户说明。
- 还要检查**词序**：泰语是"导师+ของฉัน"，日/韩是"宾语+动词"，拼颜色分工/分行时别按英语语序硬套。
