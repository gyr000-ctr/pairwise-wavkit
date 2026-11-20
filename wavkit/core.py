"""wavkit 核心 API：可增量喂入的 WAV（RIFF/WAVE）流式解析器。

解析器内部是一个小状态机：

    riff  ->  chunk_header  ->  chunk_payload  ->  (chunk_pad)  ->  chunk_header ...

每次 ``feed`` 只消费缓冲区里已经完整的部分，把不完整的部分留在缓冲区里，
因此任意切分喂入与一次性整包喂入得到的结果完全一致。RIFF 要求每个 chunk
负载按偶数字节对齐，奇数字节负载后有一个填充字节；填充字节同样可能跨
分片，需要单独等待，不能当成下一个 chunk 的开头。

样本按 fmt 中的位深逐样本解码，不足一个样本的尾部字节留在待解析缓冲区，
等下一个分片补齐；因此 24 位样本跨分片不会被截断。-1 等负值通过
``int.from_bytes(..., signed=True)`` 正确做符号扩展。
"""

import struct

_RIFF_MAGIC = b"RIFF"
_WAVE_MAGIC = b"WAVE"

# fmt chunk 固定部分长度（PCM）
_FMT_MIN_SIZE = 16


class WavStream:
    def __init__(self):
        """构造解析器。"""
        self._buf = bytearray()

        # 状态：riff / chunk_header / chunk_payload / chunk_pad
        self._state = "riff"

        self._finalized = False
        self._failed = False
        self._error = None

        self._total = 0          # 累计喂入字节数
        self._riff_size = None   # RIFF 头里声明的尺寸（不含前 8 字节）

        self._chunks = []        # 已识别 chunk 的元信息
        self._samples = []       # 已解析样本
        self._pending_sample = bytearray()  # 不足一个样本的尾部字节

        self._fmt = None
        self._have_fmt = False
        self._have_data = False
        self._data_size = 0
        self._bytes_per_sample = 0
        self._bits = 0

        self._cur_id = None
        self._cur_size = 0
        self._cur_kind = None
        self._cur_payload = None
        self._remaining = 0

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def feed(self, data):
        """喂入一段字节。可任意切分，结果与整包喂入一致。"""
        if self._finalized:
            raise ValueError("feed() called after finalize()")
        if self._failed:
            raise ValueError("stream is in a failed state: %s" % (self._error,))
        if not data:
            # 空分片直接忽略，不影响状态。
            return

        self._buf.extend(data)
        self._total += len(data)
        try:
            self._process()
        except ValueError as exc:
            self._failed = True
            self._error = exc
            raise

    def finalize(self):
        """结束解析并返回结果；重复调用会抛 ValueError。"""
        if self._finalized:
            raise ValueError("finalize() called more than once")
        if self._failed:
            raise ValueError("stream is in a failed state: %s" % (self._error,))

        try:
            self._check_complete()
        except ValueError as exc:
            self._failed = True
            self._error = exc
            raise

        self._finalized = True
        return self._result()

    def chunks(self):
        """已识别的块列表。"""
        return [dict(c) for c in self._chunks]

    def samples(self):
        """已解析样本。"""
        return list(self._samples)

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _process(self):
        buf = self._buf
        while True:
            if self._state == "riff":
                # 魔数一旦够 4 字节就立即校验，非法输入尽早报错。
                if len(buf) >= 4 and bytes(buf[:4]) != _RIFF_MAGIC:
                    raise ValueError(
                        "bad RIFF magic: %r" % (bytes(buf[:4]),)
                    )
                if len(buf) < 12:
                    return
                self._riff_size = struct.unpack_from("<I", buf, 4)[0]
                if bytes(buf[8:12]) != _WAVE_MAGIC:
                    raise ValueError(
                        "bad WAVE form type: %r" % (bytes(buf[8:12]),)
                    )
                del buf[:12]
                self._state = "chunk_header"

            elif self._state == "chunk_header":
                if len(buf) < 8:
                    return
                cid = bytes(buf[:4])
                size = struct.unpack_from("<I", buf, 4)[0]
                del buf[:8]
                self._begin_chunk(cid, size)

            elif self._state == "chunk_payload":
                if self._remaining:
                    if not buf:
                        return
                    n = min(len(buf), self._remaining)
                    piece = bytes(buf[:n])
                    del buf[:n]
                    self._remaining -= n
                    self._on_payload(piece)
                    if self._remaining:
                        # 分片用尽，等下一次 feed。
                        return
                # 负载收完：奇数长度 chunk 之后还有一个填充字节。
                if self._cur_size % 2:
                    self._state = "chunk_pad"
                else:
                    self._end_chunk()
                    self._state = "chunk_header"

            elif self._state == "chunk_pad":
                if not buf:
                    return
                # 填充字节内容按规范为 0，但解析器不依赖其取值。
                del buf[:1]
                self._end_chunk()
                self._state = "chunk_header"

            else:  # pragma: no cover - 状态机不变量
                raise AssertionError("invalid parser state: %r" % (self._state,))

    def _begin_chunk(self, cid, size):
        if cid == b"fmt ":
            if size < _FMT_MIN_SIZE:
                raise ValueError("fmt chunk too short: %d bytes" % (size,))
            kind = "fmt"
            self._cur_payload = bytearray()
        elif cid == b"data":
            if not self._have_fmt:
                raise ValueError("data chunk encountered before fmt chunk")
            kind = "data"
            self._have_data = True
            self._cur_payload = None
        else:
            kind = "skip"
            self._cur_payload = None

        self._cur_id = cid
        self._cur_size = size
        self._cur_kind = kind
        self._remaining = size
        self._state = "chunk_payload"

        self._chunks.append({"id": cid.decode("latin-1"), "size": size})

    def _on_payload(self, piece):
        if self._cur_kind == "fmt":
            self._cur_payload.extend(piece)
        elif self._cur_kind == "data":
            self._data_size += len(piece)
            self._pending_sample.extend(piece)
            self._take_samples()
        # 其它 chunk 的负载直接丢弃。

    def _end_chunk(self):
        if self._cur_kind == "fmt":
            self._parse_fmt(bytes(self._cur_payload))
        self._cur_kind = None
        self._cur_payload = None
        self._cur_id = None
        self._cur_size = 0

    # ------------------------------------------------------------------
    # 格式与样本
    # ------------------------------------------------------------------
    def _parse_fmt(self, payload):
        (format_tag, channels, sample_rate, _byte_rate,
         _block_align, bits) = struct.unpack_from("<HHIIHH", payload, 0)

        if format_tag != 1:
            raise ValueError("unsupported WAV format tag: %d" % (format_tag,))
        if channels == 0:
            raise ValueError("fmt chunk declares zero channels")
        if bits == 0 or bits % 8 != 0:
            raise ValueError("unsupported sample width: %d bits" % (bits,))

        self._fmt = {
            "format_tag": format_tag,
            "channels": channels,
            "sample_rate": sample_rate,
            "bits": bits,
        }
        self._bits = bits
        self._bytes_per_sample = bits // 8
        self._have_fmt = True

    def _take_samples(self):
        width = self._bytes_per_sample
        if width <= 0:
            return
        pending = self._pending_sample
        count = len(pending) // width
        if not count:
            return
        # WAV 中 8 位样本是无符号的，更宽的是有符号小端。
        signed = self._bits > 8
        for i in range(count):
            raw = bytes(pending[i * width:(i + 1) * width])
            self._samples.append(
                int.from_bytes(raw, "little", signed=signed)
            )
        del pending[:count * width]

    # ------------------------------------------------------------------
    # 收尾校验
    # ------------------------------------------------------------------
    def _check_complete(self):
        if self._state == "riff" and self._riff_size is None:
            raise ValueError("truncated RIFF/WAVE header")
        if self._state == "chunk_payload" and self._remaining:
            raise ValueError(
                "truncated chunk %r: %d payload bytes missing"
                % (self._cur_id, self._remaining)
            )
        if self._state == "chunk_pad":
            raise ValueError("truncated chunk padding")
        if self._state == "chunk_header" and self._buf:
            raise ValueError("truncated chunk header")

        expected = 8 + self._riff_size
        if self._total != expected:
            raise ValueError(
                "RIFF size mismatch: header declares %d bytes, got %d"
                % (expected, self._total)
            )

        if not self._have_fmt:
            raise ValueError("missing fmt chunk")
        if not self._have_data:
            raise ValueError("missing data chunk")
        if self._pending_sample:
            raise ValueError(
                "data chunk ends with a partial sample (%d trailing bytes)"
                % (len(self._pending_sample),)
            )

    def _result(self):
        result = {
            "chunks": self.chunks(),
            "samples": self.samples(),
            "data_size": self._data_size,
        }
        if self._fmt is not None:
            result.update(self._fmt)
        return result
