# pairwise-wavkit

## 题面（可直接粘贴）

入库服务改成边收边解后开始丢帧：任意分片喂入的结果与整包解析不一致、奇数长度 chunk 跨分片时错位、24 位样本在分片边界被截断、重复 finalize 不报错。wavkit/core.py 是核心，tests/test_core.py 15 个红。要修的是：任意分片 feed 与整包结果一致；chunk 偶字节对齐跨分片正确；24 位符号扩展正确；截断、非法魔数、fmt 晚于 data、RIFF 尺寸不符抛 ValueError；finalize 后继续 feed 或重复 finalize 抛 ValueError。

## 运行方式

```bash
python -m pytest tests -q
```

初始结果应为 `15 failed`（全部因 `NotImplementedError` 失败，无 collection error）。

## 字段

- 所属领域：游戏、图形与多媒体
- 子方向：音视频处理
- 任务类型：Bugfix
- 语言/框架：Python 3, pytest
- 环境可复现等级：无外部依赖
