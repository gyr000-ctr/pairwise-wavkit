"""wavkit 核心 API（初始未实现）。"""


class WavStream:
    def __init__(self):
        """构造解析器"""
        raise NotImplementedError

    def feed(self, data):
        """喂入一段字节"""
        raise NotImplementedError

    def finalize(self):
        """结束解析并返回结果"""
        raise NotImplementedError

    def chunks(self):
        """已识别的块列表"""
        raise NotImplementedError

    def samples(self):
        """已解析样本"""
        raise NotImplementedError
