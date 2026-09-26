# 数字人文文本校勘

这是一个 Python 标准库实现的校勘工作台，使用 SQLite 保存作品、版本、残片、转录、段落、异文、注释、修订层和快照，并通过 `http.server` 暴露 JSON API。

## 启动与测试

```bash
python app.py
python -m unittest discover -s tests -v
```

默认端口 `8114`，地址 <http://127.0.0.1:8114>。首次启动创建一个带缺页残片和不可辨标记的示例。数据库可通过 `COLLATION_DB` 指定，端口可通过 `PORT` 指定。

## 业务规则

- 版本类型限定为 `version`、`fragment`、`transcription`。
- 段落和版本必须属于同一作品；同一版本重复对齐同一段落会更新文本并刷新缺口清单，不会累加。
- 只有负责人或被单独授权的编辑可以修改对应版本；其他用户只有查看权限。
- `[缺页]`、`[不可辨]`、`[残损]` 等标记会参与校勘稿导出和缺口统计，不匹配的方括号会拒绝保存。
- 保存或重对齐时按版本和段落识别 `[缺页]`、`[不可辨]`、`[残损]` 三类标记，生成逐条缺口清单；同一处只保留出现次数和当前修订，重复保存不累加，已写的处理说明会保留。
- 负责人为每条缺口写处理说明，状态分为 待补、据实缺失、已说明；还有未处理说明的段落不允许定稿。
- 每次新增或修改异文都会产生递增修订号和 JSON 快照；提交必须携带 `expected_revision`，旧页面不能覆盖新层。
- 锁定段落由负责人执行，锁定后任何新修订都会被拒绝。

## 主要接口

- `POST /api/users`、`POST /api/works`
- `POST /api/works/{id}/witnesses`、`POST /api/witnesses/{id}/editors`
- `POST /api/works/{id}/passages`、`POST /api/works/{id}/access`
- `POST /api/alignments`
- `POST /api/variants`、`POST /api/variants/{id}/revisions`
- `GET /api/passages/{id}/snapshots/{revision}?user_id=...`
- `POST /api/passages/{id}/lock`
- `GET /api/works/{id}/gaps?user_id=...`、`POST /api/gaps/{id}/disposition`
- `GET /api/works/{id}/collation?user_id=...`

缺口清单接口按版本展开明细并给出待办数量；导出接口把版本对齐、异文、注释、缺口清单及处理状态、锁定状态组合成可复核的校勘稿。
