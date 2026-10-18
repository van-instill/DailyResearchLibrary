"""把中文解读里的数字和英文术语统一包成 LaTeX，交给前端 KaTeX 渲染。

为什么只包「数字」和「术语缩写」，不全包英文单词：
在 KaTeX 里 `$...$` 是数学模式，普通英文单词进去会变成斜体数学变量，
中文句子中间夹一串斜体字母反而更乱，也不符合论文排版习惯。
真正带来「整齐」的是这两类：
  - 数字 —— 数学模式下是正体 + 等宽数字（tabular figures），小数点能在纵向上对齐；
  - 术语缩写（HAR / GARCH / DiD / VaR / GB2）—— 数学模式下正体直立，不会忽大忽小。
所以 `high-frequency`、`trading` 这类普通词保持正文，只有缩略语和数字进数学模式。

`\\text{}` 是必需的：不加的话在数学模式里会被当成四个变量 $H \\cdot A \\cdot R$ 相乘，
字距被拉开，看着像乘法。
"""

from __future__ import annotations

import re

# 已有的数学段先切出来原样保留，保证函数可以重复调用（幂等）。
_MATH_SPLIT = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$)", re.S)

# 数字：允许千分位、小数、正负号（含 Unicode 负号）。
# 前一个字符不能是字母或数字，避免从 `HAR2` 里切出 `2`、从 `0.42` 里切出 `42`。
# 百分号必须放在 num 组**外面**，否则 group("num") 会把 % 一起吃掉，后面再补一个就变成 5%\%。
_NUM = r"(?<![A-Za-z0-9.])(?:[-−+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−+]?\d+(?:\.\d+)?)"

# 缩写：连续两个以上大写（HAR / GARCH / GB2）、驼峰式（DiD / VaR），
# 或小写前缀的驼峰术语（mfBm / fBm / eGARCH / mSNMMs）；
# 可带 GARCH(1,1) / AR(2) 这类参数；括号里允许逗号，否则逗号会在数学模式外被单独留在正文。
# 单个大写字母开头后接小写的普通英文单词（The / In / Trading / Hurst）不会命中。
_ABBR = (
    r"(?<![A-Za-z])(?:[A-Z]{2,}[A-Za-z]*[0-9]*"
    r"|[A-Z][a-z]+[A-Z][A-Za-z0-9]*"
    r"|[a-z]{1,3}[A-Z][A-Za-z0-9]+)"
    r"(?![A-Za-z])(?:\([0-9A-Za-z,]{1,6}\))?"
)

# 这几个是产品名，不是术语；包进数学模式只会把字体从无衬线换成衬线，看着更乱。
_ABBR_SKIP = {"arXiv", "eBay", "iPhone", "iPad", "macOS", "JavaScript"}

# 模型偶尔会把术语直接写成 $HAR$：数学模式里会被当成三个变量相乘，渲染成斜体 H·A·R。
# 只处理单美元符号、且整段就是一个术语的情况，$$\dots$$ 不动。
_MATH_ABBR = re.compile(r"(?<!\$)\$([A-Z]{2,}[A-Za-z0-9]*(?:\(\d+\))?)\$(?!\$)")

_TOKEN = re.compile(f"(?P<num>{_NUM})(?P<pct>\\s*%)?|(?P<abbr>{_ABBR})")

# `\text{}` 里只需要这几个字符是安全的；缩写本身不会出现别的字符，
# 但括号里的参数可能带 `{}` 以外的东西，统一做一次白名单清洗更保险。
_TEXT_SAFE = re.compile(r"[^A-Za-z0-9(),\-]")


def _wrap(match: re.Match[str]) -> str:
    if match.group("num") is not None:
        body = match.group("num").replace("−", "-").replace(",", "{,}")
        if match.group("pct"):
            # 数学模式下 % 是注释符，必须转义
            body += r"\%"
        return f"${body}$"

    abbr = _TEXT_SAFE.sub("", match.group("abbr"))
    if not abbr or abbr in _ABBR_SKIP:
        return match.group(0)
    return rf"$\text{{{abbr}}}$"


def latexify_text(text: str) -> str:
    """把一段中英混排文本里的数字与英文缩写包成 LaTeX。幂等。"""
    if not text:
        return text
    # `$` 数量为奇数说明原文里有未闭合的美元符号，硬拆会把半句话当成公式，
    # 这种情况直接放行，宁可少渲染也不能把正文吃掉。
    if text.count("$") % 2:
        return text

    parts = _MATH_SPLIT.split(text)
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            # 已经存在的数学段：只纠正「整段是一个术语」的情况，其余原样保留
            out.append(_MATH_ABBR.sub(lambda m: rf"$\text{{{m.group(1)}}}$", part))
        else:
            out.append(_TOKEN.sub(_wrap, part))
    return "".join(out)


SUMMARY_TEXT_FIELDS = ("problem", "method", "innovation", "conclusion", "abstract_zh", "why_relevant")


def latexify_summary(summary: object) -> object:
    """对 chinese_summary 字典里的中文字段逐个处理，返回新字典。"""
    if not isinstance(summary, dict):
        return summary
    return {
        key: latexify_text(value) if key in SUMMARY_TEXT_FIELDS and isinstance(value, str) else value
        for key, value in summary.items()
    }
