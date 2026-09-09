# -*- coding: utf-8 -*-
"""
NBT (Named Binary Tag) 解析器 —— Minecraft 的二进制数据结构。

支持全部 12 种标签：
  0 End  1 Byte  2 Short  3 Int  4 Long  5 Float  6 Double
  7 Byte_Array  8 String  9 List  10 Compound  11 Int_Array  12 Long_Array

要点（设计总纲·坑）：
  - List 标签的子元素：只有一个 payload，无名称字段、无类型字节
    （类型由 List 头部的 type 字节决定）。
  - 所有数值 = 大端（big-endian），有符号。
"""
import struct


class NbtError(Exception):
    pass


def _unpack(fmt, data, pos):
    """从 data[pos:] 解包 fmt，返回 (标量值, 新pos)。"""
    size = struct.calcsize(fmt)
    if pos + size > len(data):
        raise NbtError("NBT 数据不足: 需要 %d 字节，%d 处只剩 %d" % (size, pos, len(data) - pos))
    vals = struct.unpack_from(fmt, data, pos)
    return vals[0], pos + size


class _Reader:
    """字节流读取器，封装大端有符号数值 / 变长字符串读取。"""
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_tag_type(self):
        v, self.pos = _unpack(">b", self.data, self.pos)
        return v

    def read_byte(self):
        v, self.pos = _unpack(">b", self.data, self.pos)
        return v

    def read_unsigned_short(self):
        v, self.pos = _unpack(">H", self.data, self.pos)
        return v

    def read_int(self):
        v, self.pos = _unpack(">i", self.data, self.pos)
        return v

    def read_long(self):
        v, self.pos = _unpack(">q", self.data, self.pos)
        return v

    def read_float(self):
        v, self.pos = _unpack(">f", self.data, self.pos)
        return v

    def read_double(self):
        v, self.pos = _unpack(">d", self.data, self.pos)
        return v

    def read_string(self):
        length = self.read_unsigned_short()
        if self.pos + length > len(self.data):
            raise NbtError("字符串长度越界: %d" % length)
        s = self.data[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return s

    def read_payload(self, tag_type):
        """读取一个『无名称』的 payload（用于 List 子元素 / 顶层）。"""
        if tag_type == 1:      # Byte
            return self.read_byte()
        if tag_type == 2:      # Short
            v, self.pos = _unpack(">h", self.data, self.pos)
            return v
        if tag_type == 3:      # Int
            return self.read_int()
        if tag_type == 4:      # Long
            return self.read_long()
        if tag_type == 5:      # Float
            return self.read_float()
        if tag_type == 6:      # Double
            return self.read_double()
        if tag_type == 7:      # Byte_Array: int32 长度 + 字节
            length = self.read_int()
            if length < 0 or self.pos + length > len(self.data):
                raise NbtError("ByteArray 长度非法: %d" % length)
            raw = self.data[self.pos:self.pos + length]
            self.pos += length
            return raw
        if tag_type == 8:      # String
            return self.read_string()
        if tag_type == 9:      # List: int8 元素类型 + int32 元素数 + 元素 payload
            elem_type = self.read_tag_type()
            count = self.read_int()
            if count < 0:
                raise NbtError("List 长度非法: %d" % count)
            return [self.read_payload(elem_type) for _ in range(count)]
        if tag_type == 10:     # Compound: 一系列『命名』tag，直到 End(0)
            return self.read_named_payload()
        if tag_type == 11:     # Int_Array: int32 长度 + int32*N
            length = self.read_int()
            if length < 0:
                raise NbtError("IntArray 长度非法: %d" % length)
            return [self.read_int() for _ in range(length)]
        if tag_type == 12:     # Long_Array: int32 长度 + int64*N
            length = self.read_int()
            if length < 0:
                raise NbtError("LongArray 长度非法: %d" % length)
            return [self.read_long() for _ in range(length)]
        raise NbtError("未知 NBT 标签类型: %d" % tag_type)

    def read_named_payload(self):
        """读取 Compound：先读 name 再读 payload，直到读到 End。返回 dict。"""
        result = {}
        while True:
            tag_type = self.read_tag_type()
            if tag_type == 0:  # End
                break
            name = self.read_string()
            result[name] = self.read_payload(tag_type)
        return result


def parse_nbt(data):
    """
    解析一段 NBT 数据，返回顶层 Compound 的 dict（去掉根名）。

    Minecraft 顶层通常是一个 root compound（名字常为空字符串 ""）。
    """
    reader = _Reader(data)
    tag_type = reader.read_tag_type()
    if tag_type == 0:
        return {}
    if tag_type != 10:
        raise NbtError("顶层应是 Compound(10)，实际是 %d" % tag_type)
    reader.read_string()  # 跳过根名
    return reader.read_named_payload()
