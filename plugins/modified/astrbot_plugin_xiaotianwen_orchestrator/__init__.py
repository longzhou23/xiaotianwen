"""小天文编排层的纯协议与影子模式实现。

包根目录刻意不导入 ``main``。这样 Group Chat Plus、ContextAware、
ImageContextPool 以及单元测试可以只导入 contracts，而无需安装或启动
AstrBot 运行时。
"""

__all__ = ["contracts", "context", "ingress"]
