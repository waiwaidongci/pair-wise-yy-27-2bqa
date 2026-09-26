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
- 段落和版本必须属于同一作品，同一版本不能重复对齐同一段落。
- 只有负责人或被单独授权的编辑可以修改对应版本；其他用户只有查看权限。
- `[缺页]`、`[不可辨]`、`[残损]` 三类标记会在保存或重对齐时按「版本+段落+标记」自动对账为逐条缺口：记录出现次数和当前文本，重复保存不累加；标记消失则删除，标记仍在则保留处理说明。
- 每条缺口由负责人写处理说明并标注状态：`confirmed`（据实缺失）、`pending`（待补）、`explained`（已说明），说明不能为空。
- 段落存在未写处理说明的缺口时禁止锁定定稿。
- 每次新增或修改异文都会产生递增修订号和 JSON 快照；提交必须携带 `expected_revision`，旧页面不能覆盖新层。
- 锁定段落由负责人执行，锁定后任何新修订和重对齐都会被拒绝。

## 主要接口

- `POST /api/users`、`POST /api/works`
- `POST /api/works/{id}/witnesses`、`POST /api/witnesses/{id}/editors`
- `POST /api/works/{id}/passages`、`POST /api/works/{id}/access`
- `POST /api/alignments`（同一版本+段落重复保存为重对齐，幂等更新并对账缺口）
- `POST /api/variants`、`POST /api/variants/{id}/revisions`
- `GET /api/passages/{id}/snapshots/{revision}?user_id=...`
- `POST /api/passages/{id}/lock`
- `GET /api/works/{id}/gaps?user_id=...`：按版本展开缺口明细、待办数量及段落定稿状态
- `POST /api/gaps/{id}/handling`：负责人写处理说明（`disposition`、`note`）
- `GET /api/works/{id}/collation?user_id=...`

导出接口把版本对齐、异文、注释、缺口清单（逐条标记、出现次数、当前修订、处理状态与说明、按版本汇总的待办数）和锁定状态组合成可复核的校勘稿。
