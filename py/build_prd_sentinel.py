# -*- coding: utf-8 -*-
"""生成《商客对话监测 · 产品需求文档 v1.0》docx。"""
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from pathlib import Path
OUT = str(Path(__file__).resolve().parent / "商客对话监测_PRD_v1.docx")
GRAY = RGBColor(0x5C, 0x5C, 0x5C)
BLACK = RGBColor(0, 0, 0)
EA, LATIN = "微软雅黑", "Calibri"


def style_run(run, size=10.5, bold=False, color=BLACK):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = LATIN
    rpr = run._element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:ascii"), LATIN)
    rpr.rFonts.set(qn("w:hAnsi"), LATIN)
    rpr.rFonts.set(qn("w:eastAsia"), EA)


def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def cell_width(cell, cm):
    tcPr = cell._tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)
    tcW.set(qn("w:w"), str(int(cm * 567)))
    tcW.set(qn("w:type"), "dxa")


def h1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    style_run(p.add_run(text), size=14.5, bold=True)
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single"); b.set(qn("w:sz"), "6")
    b.set(qn("w:space"), "2"); b.set(qn("w:color"), "D9D9D9")
    pbdr.append(b); pPr.append(pbdr)


def h2(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    style_run(p.add_run(text), size=11.5, bold=True)


def para(doc, text, size=10.5, space_after=4, bold=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.45
    style_run(p.add_run(text), size=size, bold=bold)
    return p


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.4
    p.paragraph_format.left_indent = Cm(0.85)
    p.paragraph_format.first_line_indent = Cm(-0.45)
    style_run(p.add_run("· "), size=size)
    style_run(p.add_run(text), size=size)


def table(doc, headers, rows, widths, size=8.8):
    total = float(sum(widths))
    if total > 16.4:
        widths = [w * 16.4 / total for w in widths]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    t._tbl.tblPr.append(layout)
    for gc, wcm in zip(t._tbl.tblGrid.gridCol_lst, widths):
        gc.set(qn("w:w"), str(int(wcm * 567)))
    hdr = t.rows[0].cells
    for i, htext in enumerate(headers):
        cell = hdr[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.15
        style_run(p.add_run(htext), size=size, bold=True)
        shade(cell, "F2F2F2")
        cell_width(cell, widths[i])
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cell = cells[i]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.2
            style_run(p.add_run(str(val)), size=size)
            cell_width(cell, widths[i])
    trs = t._tbl.findall(qn("w:tr"))
    for idx, tr in enumerate(trs):
        trPr = tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
        if idx == 0:
            trPr.append(OxmlElement("w:tblHeader"))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def caption(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    style_run(p.add_run(text), size=8.5, color=GRAY)


doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.top_margin = sec.bottom_margin = Cm(2.2)
sec.left_margin = sec.right_margin = Cm(2.3)
normal = doc.styles["Normal"]
normal.font.name = LATIN
normal.font.size = Pt(10.5)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), EA)

# ---------------------------------------------------------------- 封面
p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(6)
p.paragraph_format.space_after = Pt(2)
style_run(p.add_run("产品需求文档 PRD"), size=9, color=GRAY)
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
style_run(p.add_run("商客对话监测"), size=24, bold=True)
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(10)
style_run(p.add_run("消费者与商家会话中的不公平话术检测与介入"), size=12.5, color=GRAY)
table(doc, ["版本", "作者", "日期", "状态", "适用范围"],
      [["v1.0", "潘浩林", "2026-11-10", "草案（Demo 阶段）", "单平台内测，不含商家侧改造"]],
      [2.0, 2.6, 3.0, 4.2, 4.5])
para(doc, "本文档定义「商客对话监测」一期（Demo）的问题、范围、判定规则、介入流程与指标口径。"
          "配套材料：《商客对话监测_MVP原型方案.md》（五屏原型）、《README.md》（可运行 Demo）。", size=9.5)

# ---------------------------------------------------------------- 1
h1(doc, "1  项目概述")

h2(doc, "1.1  背景")
para(doc, "平台对交易纠纷的处理是事后的：买家申请退款、平台介入仲裁、最后判定责任。"
          "但不公平往往发生在私聊里的一两句话上——「特价商品不退不换」「你先把差评删了我就退」"
          "「签收就代表认可」。消费者听不懂这些说法背后的法律含义，多数人当场就放弃了。")
para(doc, "等到申请平台介入时，导火索那句话已经淹没在几十条消息里，消费者也很难证明"
          "「对方是怎么让我放弃的」。现有产品只在两个点上做事：事前给规则文档，事后做仲裁。"
          "中间那段「对话正在发生」的时间是空的。")

h2(doc, "1.2  问题定义")
table(doc, ["现状", "造成的后果", "本产品的处理"],
      [["不公平话术藏在正常对话里，用户无法识别",
        "消费者在不知情的情况下放弃法定权利，事后无法追溯",
        "实时识别话术类型，给出「你在被要求放弃什么」"],
       ["纠纷只能事后申诉，举证靠消费者自己",
        "对话记录零散，申诉时说不清哪句话是关键",
        "命中即固定证据快照（原话、轮次、规则、时间）"],
       ["平台规则文档没人看",
        "消费者不知道「特价不退」不是合法理由",
        "把规则翻译成一句话，挂在提醒卡上"],
       ["客服机器人只回答业务问题", "对「对方在欺负我」这类问题无能无力",
        "旁路判定，不参与对话，只在关键节点插入提醒"]],
      [5.2, 5.6, 5.6])

h2(doc, "1.3  产品目标")
table(doc, ["目标类型", "目标内容", "衡量方式"],
      [["业务目标", "把「可挽回的放弃」变成「有记录的申诉」", "介入率、申诉转化率"],
       ["体验目标", "正常沟通零打扰；介入时不打断对话，只插入一张卡", "误报率、卡片关闭率"],
       ["质量目标", "宁可漏报不可误报（误报会伤害正常交易）", "误报率、人工复核一致率"],
       ["方法目标", "规则可运营：新增一种话术不改代码", "规则上线耗时、命中分布"]],
      [2.4, 9.0, 5.4])

h2(doc, "1.4  范围外（一期不做）")
for t_ in ["商家侧提示与培训", "自动判责与自动赔付", "语音/图片消息的理解",
           "跨会话、跨平台的行为画像", "商家申诉流程", "用户主动举报入口"]:
    bullet(doc, t_)
para(doc, "砍掉这些的理由是同一个：它们都不影响「提醒了用户会不会用」这个核心假设。", size=9.5)

h2(doc, "1.5  核心难点")
table(doc, ["难点", "为什么难", "一期的处理"],
      [["判定尺度", "「不退」可能是引用规则，也可能是拒绝权利",
        "单句规则 + 负向抑制：同一句出现「可以退 / 已经安排」就不判"],
       ["误报成本不对称", "误报打断正常交易，比漏报更伤",
        "阈值偏保守；低置信只提示不动作"],
       ["话术演化", "商家换个说法就能绕开关键词",
        "规则库按月运营；留出集持续监测泛化"],
       ["隐私与合规", "读用户私聊内容",
        "只存命中片段与证据快照；判定在平台侧，不外传"]],
      [3.0, 7.0, 6.8])

# ---------------------------------------------------------------- 2
h1(doc, "2  用户与场景")

h2(doc, "2.1  用户角色")
table(doc, ["角色", "他是谁", "关心什么", "在本产品里做什么"],
      [["消费者", "正在和商家沟通的买家", "别被骗，别被绕进去", "看到提醒、决定是否申请介入"],
       ["平台风控运营", "治理与规则维护方", "哪类话术在涨、规则准不准", "维护规则、看命中分布、复核样本"],
       ["平台仲裁", "人工介入团队", "证据够不够、能不能判", "接收介入单与证据包"]],
      [2.6, 5.4, 4.6, 3.8])

h2(doc, "2.2  三个场景")
para(doc, "场景一：正常会话（不打扰）", bold=True, space_after=2)
bullet(doc, "买家问发货时间，商家答「今天下午发出，已经安排」。系统判定为正常沟通，全程无提示。")
bullet(doc, "成功标准：没有任何插入，用户感觉不到系统存在。")
para(doc, "场景二：命中并介入（关键路径）", bold=True, space_after=2)
bullet(doc, "商家说「可以给你退，你先把差评删了」。命中「以利益换好评」。")
bullet(doc, "卡片插入会话：对方可能侵犯了你的权利 + 依据条款 + 申请平台介入。")
para(doc, "场景三：证据与申诉", bold=True, space_after=2)
bullet(doc, "用户点「申请平台介入」，系统自动带上原话、轮次、规则、时间与截图。")
bullet(doc, "成功后进入进度页，可看到平台处理到哪一步。")

h2(doc, "2.3  任务地图")
table(doc, ["阶段", "消费者任务", "系统支持", "一期缺口"],
      [["沟通", "把问题说清楚", "旁路监听，不介入", "不识别语音/图片"],
       ["被劝退", "判断这句话能不能信", "命中即弹提醒卡", "不做法律解释，只给规则"],
       ["决定", "要不要申请介入", "一键申请 + 证据自动带上", "不做赔付预期"],
       ["等待", "知道进展", "介入单时间轴", "不对接真实工单系统"]],
      [2.2, 4.8, 5.6, 3.8])

# ---------------------------------------------------------------- 3
h1(doc, "3  产品结构")

h2(doc, "3.1  三个界面")
table(doc, ["界面", "给谁用", "核心内容", "判断标准"],
      [["会话内提醒", "消费者", "气泡高亮 + 提醒卡 + 两个动作", "用户能不能在三秒内看懂「我被要求放弃什么」"],
       ["介入单详情", "消费者", "证据快照、规则依据、处理时间轴", "不用重复描述就能申诉"],
       ["规则运营后台", "风控运营", "规则列表、命中分布、样本复核", "能不能看出哪条规则在误伤"]],
      [2.6, 2.8, 6.4, 4.6])

h2(doc, "3.2  主流程")
table(doc, ["步骤", "动作", "输出"],
      [["1", "消息进入（商家发送）", "原始消息 + 会话上下文"],
       ["2", "规则匹配（单句）", "命中的规则、命中词、严重度"],
       ["3", "负向抑制检查", "同一句有正常承诺则放弃判定"],
       ["4", "对话级判定（可选）", "重复类问题达到阈值才触发"],
       ["5", "生成提醒卡", "类型 + 依据条款 + 建议动作"],
       ["6", "用户选择", "申请介入 / 忽略"],
       ["7", "生成证据快照", "原话、轮次、规则、截图"],
       ["8", "创建介入单", "单号、状态、时间轴"]],
      [1.4, 7.0, 8.2])

h2(doc, "3.3  和现有能力的区别")
table(doc, ["维度", "客服机器人", "事后投诉", "商客对话监测"],
      [["时机", "用户提问时", "用户申诉后", "对话进行中"],
       ["目标", "答对问题", "判定责任", "让用户知道自己在放弃什么"],
       ["输出", "答案", "裁决", "提醒 + 证据 + 一键介入"],
       ["误报代价", "答案不准", "判错赔钱", "打断正常沟通"]],
      [2.4, 4.6, 4.6, 4.8])

# ---------------------------------------------------------------- 4
h1(doc, "4  功能需求")

h2(doc, "4.1  会话监听与判定")
table(doc, ["编号", "功能", "说明与关键规则", "验收要点"],
      [["FR-01", "消息接入", "只处理商家发出的消息；按会话聚合，带轮次序号",
        "同一会话多次命中只提醒一次"],
       ["FR-02", "单句规则匹配", "按规则库匹配关键词/否定结构；返回规则、命中词、严重度",
        "同一句命中多条时按「严重度 → 优先级」取一条"],
       ["FR-03", "负向抑制", "同一句出现「可以退/已经安排/帮您登记」等正常承诺，不判定",
        "负向样本零误报"],
       ["FR-04", "对话级判定", "同类问题累计（如连续三次拖延）才触发",
        "单次拖延不提醒，第三次才提醒"],
       ["FR-05", "置信度", "输出 0~1 的分数；低于阈值只记录不展示",
        "低置信样本不出现提醒卡"]],
      [1.4, 2.4, 8.0, 5.0])

h2(doc, "4.2  介入动作")
table(doc, ["编号", "功能", "说明与关键规则", "验收要点"],
      [["FR-06", "提醒卡", "类型 + 一句话后果 + 依据条款 + 两个按钮；三种严重度配色",
        "卡片不遮挡输入框"],
       ["FR-07", "气泡高亮", "命中那句消息加红框，保留原文不做改写", "原文可复制"],
       ["FR-08", "权利说明", "把条款翻译成「你能主张什么」，不出现法条编号以外的术语",
        "一句话能读懂"],
       ["FR-09", "查看证据", "展开证据快照：原话、轮次、规则、时间", "可截图保存"],
       ["FR-10", "忽略", "用户可关闭卡片；同一会话不再重复弹", "关闭后不再打扰"]],
      [1.4, 2.4, 8.0, 5.0])

h2(doc, "4.3  介入单与进度")
table(doc, ["编号", "功能", "说明与关键规则", "验收要点"],
      [["FR-11", "一键申请", "自动带订单号、商家、问题类型、金额、证据", "无必填项"],
       ["FR-12", "提交确认", "提交前展示将要提交的内容", "可补充一句话说明"],
       ["FR-13", "进度时间轴", "已提交/已受理/商家举证/平台判定/处理完成", "只有前两步是完成态"],
       ["FR-14", "结果通知", "判定结果回到会话，附一句话说明", "不打断会话"]],
      [1.4, 2.4, 8.0, 5.0])

h2(doc, "4.4  规则运营")
table(doc, ["编号", "功能", "说明与关键规则", "验收要点"],
      [["FR-15", "规则列表", "按类型/严重度/状态筛选，显示命中次数", "能看到哪条规则在误伤"],
       ["FR-16", "新增规则", "配置触发词、依据条款、提示文案、动作，保存即生效", "不改代码上线"],
       ["FR-17", "样本复核", "抽查命中样本，标记「准确/误报」", "误报率可计算"],
       ["FR-18", "效果看板", "召回、误报、命中分布、介入率", "按周对比"]],
      [1.4, 2.4, 8.0, 5.0])

# ---------------------------------------------------------------- 5
h1(doc, "5  判定规则")

h2(doc, "5.1  规则结构")
para(doc, "一条规则 = 类型 + 严重度 + 触发条件 + 依据条款 + 消费者提示 + 平台动作。"
          "存成 JSONL，一行一条，运营后台可增删。", size=10)

h2(doc, "5.2  一期 12 条规则")
table(doc, ["优先级", "类型", "严重度", "触发（示例）", "依据", "动作"],
      [["P1", "诱导线下交易", "高", "加我微信 / 私下转", "—", "立即介入"],
       ["P1", "以利益换好评", "高", "删差评 / 改好评 / 好评返现", "—", "立即介入"],
       ["P1", "威胁或辱骂", "高", "你等着 / 投诉也没用 / 爱要不要", "—", "立即介入"],
       ["P1", "索要不必要的身份信息", "高", "验证码 / 身份证照片 / 银行卡号", "—", "立即介入"],
       ["P2", "扩大退货例外", "中", "拆封不退 / 拆开不退", "RETURN_7.3", "提示权利"],
       ["P2", "诱导放弃权利", "中", "签收即认可 / 详情页写了 / 概不退换", "RETURN_7.1", "提示权利"],
       ["P2", "拒绝法定权利", "中", "特价不退 / 不退不换（含否定结构）", "RETURN_7.1", "提示权利"],
       ["P2", "拒绝开票", "中", "不开票 / 开不了发票", "INVOICE_10.1", "提示权利"],
       ["P3", "责任推诿", "中", "物流的问题 / 签收就代表", "QUALITY_9.1", "提示权利"],
       ["P4", "反复拖延", "低", "明天回复 / 稍等（累计 3 次）", "SERVICE_16.1", "提示"],
       ["P5", "虚假承诺", "低", "假一赔十 / 保证正品", "QUALITY_9.1", "提示"],
       ["—", "正常沟通（抑制）", "无", "已经安排 / 帮您登记 / 可以退", "—", "不介入"]],
      [1.3, 3.4, 1.3, 5.2, 2.6, 2.0], size=8.2)
caption(doc, "说明：严重度决定卡片配色与动作强度；优先级用于同一句命中多条规则时的裁决。")

h2(doc, "5.3  判定顺序")
para(doc, "① 先过负向规则：同一句出现正常承诺 → 直接放弃判定。"
          "② 再过正向规则：命中多条时按「严重度 → 优先级」取一条。"
          "③ 对话级规则（拖延类）在单句规则之后判定，需要累计次数达标。", size=10)

# ---------------------------------------------------------------- 6
h1(doc, "6  指标定义")

h2(doc, "6.1  离线指标（当前 Demo 已在算）")
table(doc, ["指标", "定义", "当前值", "说明"],
      [["介入召回率", "应介入的对话中被检出的比例", "开发集 100% / 留出集一 75% / 留出集二 75%", "开发集为过拟合，留出集才是参考"],
       ["误报率", "正常对话被误判的比例", "三组均为 0%", "靠负向抑制"],
       ["类型准确率", "检出的类型与标注一致", "100% / 100% / 67%", "第二组出现类型混判"],
       ["首响轮次", "第一次介入发生在第几轮", "平均 1.8 / 1.0 / 1.0", "越小越好"],
       ["归并正确率", "同一对话多次命中只提醒一次", "100%", "—"]],
      [3.0, 5.6, 4.4, 3.4])

h2(doc, "6.2  线上指标（待接入真实流量）")
table(doc, ["指标", "口径", "目标", "为什么看它"],
      [["介入采纳率", "点了申请介入 / 看到卡片的会话", "≥ 15%", "衡量提醒有没有用"],
       ["卡片关闭率", "用户主动关闭 / 弹出", "≤ 40%", "过高说明在打扰"],
       ["申诉转化率", "介入单最终成立 / 提交", "≥ 50%", "衡量证据够不够"],
       ["规则覆盖率", "命中样本 / 人工复核判定为不公平的样本", "≥ 80%", "衡量规则库是否跟得上话术"]],
      [2.8, 6.0, 2.4, 5.2])

h2(doc, "6.3  为什么召回和误报要一起看")
para(doc, "只看召回，最优解是把所有「不退」都拦下来，正常售后会被打断；"
          "只看误报，最优解是几乎不提醒，产品等于没做。"
          "本产品的取舍是：宁可漏报，不可误报——误报伤害的是正常交易，漏报伤害的是单个用户。", size=10)

# ---------------------------------------------------------------- 7
h1(doc, "7  非功能需求")
table(doc, ["类别", "要求", "说明"],
      [["时延", "商家消息到卡片出现 ≤ 500ms", "旁路判定，不阻塞消息发送"],
       ["可用性", "判定服务异常时静默降级，不影响聊天", "宁可漏判，不能打断交易"],
       ["隐私", "只存命中片段与证据快照；不存完整会话明文超过 30 天", "需与法务确认留存策略"],
       ["可审计", "每次命中记录规则、命中词、分数、展示结果", "支撑误报复盘"],
       ["可配置", "规则、阈值、文案均可在后台改，不改代码", "运营能自己迭代"]],
      [2.4, 8.4, 5.6])

# ---------------------------------------------------------------- 8
h1(doc, "8  数据需求")

h2(doc, "8.1  规则结构（JSONL）")
table(doc, ["字段", "类型", "说明", "示例"],
      [["id", "string", "规则 ID", "R04"],
       ["type", "string", "不公平类型", "以利益换好评"],
       ["severity", "enum", "high / mid / low", "high"],
       ["priority", "int", "同严重度内的裁决顺序", "1"],
       ["patterns", "array", "触发词（可含否定结构）", "删差评 / 改好评"],
       ["rights / denials", "array", "「权利词 + 拒绝词」组合触发时使用", "退 / 不退"],
       ["clause", "string", "依据条款 ID（可为空）", "RETURN_7.1"],
       ["hint", "string", "给消费者看的一句话", "以退款要挟删差评，平台禁止"],
       ["action", "string", "提示 / 提示权利 / 立即介入", "立即介入"]],
      [2.8, 2.2, 6.0, 5.4])

h2(doc, "8.2  标注集结构")
table(doc, ["字段", "说明"],
      [["scenario_id", "对话编号"],
       ["turns[]", "逐轮消息（role=user/merchant，text）"],
       ["expect.intervene", "是否应当介入"],
       ["expect.type", "应当判定的类型"],
       ["expect.turn", "应当首次介入的轮次（从 0 开始）"]],
      [3.4, 9.0])

h2(doc, "8.3  当前数据规模")
para(doc, "规则 12 条；标注对话 24 段（开发集 12、留出集一 6、留出集二 6）；"
          "其中应介入 18 段、不应介入 6 段。样本量只能验证链路，不能代表线上效果。", size=10)

# ---------------------------------------------------------------- 9
h1(doc, "9  接口")
table(doc, ["方法", "路径", "说明", "主要字段"],
      [["POST", "/api/hook/message", "平台推送商家消息", "session_id, turn, text, order_id"],
       ["POST", "/api/judge", "对单条消息判定", "返回 rule_id / type / severity / score"],
       ["POST", "/api/case", "创建介入单", "证据快照 + 用户补充说明"],
       ["GET", "/api/case/{id}", "查询介入进度", "状态、时间轴、结果"]],
      [1.4, 4.4, 4.2, 6.4])

# ---------------------------------------------------------------- 10
h1(doc, "10  演示要求（对齐五屏原型）")
table(doc, ["屏", "内容", "演示要点"],
      [["01", "正常会话", "全程无提醒——先证明它安静"],
       ["02", "提醒卡插入", "卡片不遮挡输入框，三秒能读懂"],
       ["03", "权利依据", "条款编号与卡片一致"],
       ["04", "提交证据", "证据数量与卡片说明一致（3 张截图 + 7 条消息）"],
       ["05", "介入进度", "只有前两步是完成态"]],
      [1.4, 4.2, 9.0])

# ---------------------------------------------------------------- 11
h1(doc, "11  验收标准")
table(doc, ["编号", "验收项", "标准", "验证方式"],
      [["AC-01", "正常会话零打扰", "6 段正常对话全部不介入", "跑留出集中的负向样本"],
       ["AC-02", "核心类型可检出", "拒绝权利、诱导线下、删差评、威胁、隐私五类全部命中", "逐类各跑 2 条"],
       ["AC-03", "卡片不遮挡", "提醒卡出现时输入框仍可见可点", "原型走查"],
       ["AC-04", "证据完整", "介入单包含原话、轮次、规则、时间", "提交后检查字段"],
       ["AC-05", "低置信不打扰", "分数低于阈值只记录不展示", "构造边界样本"],
       ["AC-06", "规则可运营", "后台新增一条规则后立即生效", "新增「不支持改地址」验证"],
       ["AC-07", "异常降级", "判定服务超时，聊天不受影响", "断网后发消息"]],
      [1.6, 3.4, 6.4, 5.0])

# ---------------------------------------------------------------- 12
h1(doc, "12  里程碑")
table(doc, ["阶段", "时间", "目标", "验收物"],
      [["第 1 周", "D1–D7", "规则库与标注集", "12 条规则、24 段标注对话"],
       ["第 2 周", "D8–D14", "判定引擎与五屏原型", "可跑脚本 + Figma 原型"],
       ["第 3 周", "D15–D21", "介入单与后台", "后台可增删规则、看命中分布"],
       ["第 4 周", "D22–D28", "评测与演示", "三组指标、30 秒录屏"]],
      [2.0, 2.4, 5.4, 8.2])

# ---------------------------------------------------------------- 13
h1(doc, "13  风险与对策")
table(doc, ["风险", "影响", "对策"],
      [["误报打断正常交易", "用户反感、商家投诉", "负向抑制 + 阈值保守 + 卡片可关闭"],
       ["话术演化绕过规则", "召回逐轮下降", "规则月度运营；留出集持续监测"],
       ["被判「平台监控私聊」", "舆论与合规风险", "只存命中片段；对外表述为「风险提示」"],
       ["介入后无人处理", "转化率高但成功率低", "先对接仲裁人力，再放开入口"]],
      [3.6, 6.0, 6.8])

# ---------------------------------------------------------------- 14
h1(doc, "14  附录")

h2(doc, "14.1  规则示例")
para(doc, '{"id":"R04","type":"以利益换好评","severity":"high","priority":1,'
          '"patterns":["删差评","差评删","改好评","好评返现","删了评价","五星好评"],'
          '"clause":"","hint":"以退款为条件要求删差评或改好评，属于干预评价，平台规则禁止。",'
          '"action":"立即介入"}', size=8.5)

h2(doc, "14.2  对话示例（留出集二 N02，当前漏判）")
para(doc, "消费者：机器漏水，我评价写了问题", size=9)
para(doc, "商家：你把评价撤了，我马上退款", size=9)
para(doc, "说明：说的「撤了」不在规则词表里，因此没有命中。这类漏判正是关键词方案的天花板，"
          "也是下一阶段转向语义判定的依据。", size=9)

doc.core_properties.title = "商客对话监测 PRD v1.0"
doc.core_properties.author = "潘浩林"
doc.save(OUT)
print("saved", OUT)
