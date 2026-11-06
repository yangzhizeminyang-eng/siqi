"""OPT-11：main.py 渐进拆分出的领域 API 包（路由处理器按领域就近维护）。

拆分不变式（由 tests/test_opt11_main_split.py 冻结）：
- 对外路由表逐条不变；
- 处理器定义在本包领域模块，app.main 不再保留同名定义；
- 跨模块共享的服务/模型/内存真源仍留在 app.main，经模块属性在调用时读取，
  以保证既有 monkeypatch 语义（如 main.check_milvus_health / main.ROLE_PERMISSIONS）不失效。
"""
