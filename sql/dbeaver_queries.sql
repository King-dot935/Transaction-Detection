-- 商客对话监测 · DBeaver 常用查询
-- 用法：DBeaver 里连上 sentinel.db → SQL 编辑器 → 整段粘贴 → Ctrl+Enter 逐条执行

-- ========== 1. 这张表里有什么：全部表与行数 ==========
SELECT 'cases' AS 表, COUNT(*) AS 行数 FROM cases
UNION ALL SELECT 'judge_logs',        COUNT(*) FROM judge_logs
UNION ALL SELECT 'review_queue',      COUNT(*) FROM review_queue
UNION ALL SELECT 'notifications',     COUNT(*) FROM notifications
UNION ALL SELECT 'merchants',         COUNT(*) FROM merchants
UNION ALL SELECT 'rule_candidates',   COUNT(*) FROM rule_candidates
UNION ALL SELECT 'feedback',          COUNT(*) FROM feedback
UNION ALL SELECT 'eval_runs',         COUNT(*) FROM eval_runs;


-- ========== 2. 最近判定了什么（规则 / 分流层 / 原因）==========
SELECT id, ts, source, tier, rule_type, rule_hit, cost_units,
       substr(text, 1, 28) AS 话术, route_reason
FROM judge_logs
ORDER BY id DESC
LIMIT 20;


-- ========== 3. 待人工复核的（带挂起原因）==========
SELECT id, ts, rule_type AS 规则结论, tier AS 分流层, reason AS 挂起原因,
       substr(text, 1, 30) AS 话术
FROM review_queue
WHERE status = 'pending'
ORDER BY id DESC;


-- ========== 4. 人工裁决记录（谁改的、改成什么）==========
SELECT id, ts, status, reviewer, decided_at, case_no,
       rule_type AS 规则原本结论, substr(text, 1, 26) AS 话术
FROM review_queue
WHERE status <> 'pending'
ORDER BY id DESC;


-- ========== 5. 生成的介入单 + 对应通知 ==========
SELECT c.case_no, c.ts, c.status, c.merchant,
       substr(c.text, 1, 24) AS 原话,
       n.title AS 通知标题, n.user_id
FROM cases c
LEFT JOIN notifications n ON n.case_no = c.case_no
ORDER BY c.id DESC;


-- ========== 6. 规则候选池：同类累计够数就该改规则 ==========
SELECT text AS 话术,
       CASE kind WHEN 'positive' THEN '应介入' ELSE '应放行' END AS 倾向,
       hits AS 出现次数
FROM rule_candidates
WHERE status = 'open'
ORDER BY hits DESC;


-- ========== 7. 指标：自动判定率 / 待复核率 ==========
SELECT
  (SELECT COUNT(*) FROM judge_logs WHERE source <> 'human')                AS 总判定,
  (SELECT COUNT(*) FROM review_queue WHERE status = 'pending')             AS 待复核,
  ROUND(1.0 * (SELECT COUNT(*) FROM judge_logs WHERE source <> 'human')
        / NULLIF((SELECT COUNT(*) FROM judge_logs WHERE source <> 'human'), 0), 3)
                                                                           AS 自动判定率;


-- ========== 8. 人工改判率：规则原本 say no、人说要介入 ==========
SELECT
  COUNT(*) AS 已裁决,
  SUM(CASE WHEN (rule_hit = 1) <> (status = 'approved') THEN 1 ELSE 0 END) AS 改判数,
  ROUND(1.0 * SUM(CASE WHEN (rule_hit = 1) <> (status = 'approved') THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 3) AS 人工改判率
FROM review_queue
WHERE status <> 'pending';


-- ========== 9. 按分流层看成本分布 ==========
SELECT tier AS 分流层, COUNT(*) AS 条数, ROUND(AVG(cost_units), 2) AS 平均成本,
       ROUND(SUM(cost_units), 2) AS 总成本
FROM judge_logs
GROUP BY tier
ORDER BY 条数 DESC;


-- ========== 10. 按规则类型看命中分布 ==========
SELECT rule_type AS 类型, COUNT(*) AS 命中次数
FROM judge_logs
WHERE rule_hit = 1
GROUP BY rule_type
ORDER BY 命中次数 DESC;
